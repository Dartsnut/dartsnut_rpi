#include <google/cloud/firestore/firestore_client.h>
#include <google/cloud/firestore/listener.h>
#include <google/cloud/options.h>
#include <nlohmann/json.hpp>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace {

struct Config {
  std::string project_id;
  std::string device_ble_mac;
  std::string socket_path;
};

Config ParseArgs(int argc, char *argv[]) {
  Config cfg;
  const char *project_env = std::getenv("GOOGLE_CLOUD_PROJECT");
  if (project_env != nullptr)
    cfg.project_id = project_env;
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if (arg.rfind("--project-id=", 0) == 0) {
      cfg.project_id = arg.substr(std::string("--project-id=").size());
    } else if (arg.rfind("--device-ble-mac=", 0) == 0) {
      cfg.device_ble_mac = arg.substr(std::string("--device-ble-mac=").size());
    } else if (arg.rfind("--socket-path=", 0) == 0) {
      cfg.socket_path = arg.substr(std::string("--socket-path=").size());
    }
  }
  return cfg;
}

int ConnectUnixSocket(const std::string &path) {
  int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
  if (fd < 0) {
    return -1;
  }
  sockaddr_un addr;
  std::memset(&addr, 0, sizeof(addr));
  addr.sun_family = AF_UNIX;
  std::snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", path.c_str());
  if (::connect(fd, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) < 0) {
    ::close(fd);
    return -1;
  }
  return fd;
}

void SendJsonLine(int fd, nlohmann::json const &j) {
  std::string line = j.dump();
  line.push_back('\n');
  ssize_t n = ::write(fd, line.data(), line.size());
  (void)n;
}

google::cloud::firestore::DocumentReference
DocRef(google::cloud::firestore::FirestoreClient &client,
       std::string const &project_id, std::string const &device_ble_mac) {
  std::string path = "devices/" + device_ble_mac;
  return client.Document("projects/" + project_id +
                         "/databases/(default)/documents/" + path);
}

bool DocumentExists(google::cloud::firestore::FirestoreClient &client,
                    google::cloud::firestore::DocumentReference const &doc) {
  auto maybe = client.GetDocument(doc);
  if (!maybe)
    return false;
  return maybe->exists();
}

void WriteInitialState(google::cloud::firestore::FirestoreClient &client,
                       google::cloud::firestore::DocumentReference const &doc,
                       nlohmann::json const &state) {
  google::cloud::firestore::MapFieldValue data;
  for (auto const &[k, v] : state.items()) {
    if (v.is_number_integer()) {
      data[k] =
          google::cloud::firestore::FieldValue::Integer(v.get<long long>());
    } else if (v.is_number()) {
      data[k] = google::cloud::firestore::FieldValue::Double(v.get<double>());
    } else if (v.is_boolean()) {
      data[k] = google::cloud::firestore::FieldValue::Boolean(v.get<bool>());
    } else if (v.is_string()) {
      data[k] =
          google::cloud::firestore::FieldValue::String(v.get<std::string>());
    } else if (v.is_array() || v.is_object()) {
      data[k] = google::cloud::firestore::FieldValue::String(v.dump());
    }
  }
  (void)client.SetDocument(doc, data);
}

void ApplyDeviceStateUpdate(
    google::cloud::firestore::FirestoreClient &client,
    google::cloud::firestore::DocumentReference const &doc,
    nlohmann::json const &state) {
  google::cloud::firestore::MapFieldValue data;
  if (state.contains("brightness")) {
    data["brightness"] = google::cloud::firestore::FieldValue::Integer(
        state["brightness"].get<long long>());
  }
  if (state.contains("volume")) {
    data["volume"] = google::cloud::firestore::FieldValue::Integer(
        state["volume"].get<long long>());
  }
  if (state.contains("ip_address")) {
    data["ip_address"] = google::cloud::firestore::FieldValue::String(
        state["ip_address"].get<std::string>());
  }
  if (!data.empty()) {
    (void)client.UpdateDocument(doc, data);
  }
}

void RunListener(google::cloud::firestore::FirestoreClient client,
                 std::string project_id, std::string device_ble_mac,
                 int socket_fd) {
  auto doc = DocRef(client, project_id, device_ble_mac);
  auto callback =
      [socket_fd](
          google::cloud::StatusOr<google::cloud::firestore::DocumentSnapshot>
              snapshot) {
        if (!snapshot || !snapshot->exists())
          return;
        nlohmann::json payload;
        try {
          auto data = snapshot->GetData();
          for (auto const &kv : data) {
            auto const &key = kv.first;
            auto const &value = kv.second;
            if (value.is_integer()) {
              payload[key] = value.integer_value();
            } else if (value.is_double()) {
              payload[key] = value.double_value();
            } else if (value.is_string()) {
              payload[key] = value.string_value();
            } else if (value.is_boolean()) {
              payload[key] = value.boolean_value();
            }
          }
        } catch (...) {
          return;
        }
        nlohmann::json msg{
            {"kind", "config"},
            {"payload", payload},
        };
        SendJsonLine(socket_fd, msg);
      };

  (void)client.ListenToDocument(doc, std::move(callback));
}

} // namespace

int main(int argc, char *argv[]) {
  Config cfg = ParseArgs(argc, argv);
  if (cfg.project_id.empty() || cfg.device_ble_mac.empty() ||
      cfg.socket_path.empty()) {
    std::cerr << "firestore_sync: missing --project-id, --device-ble-mac, or "
                 "--socket-path\n";
    return 1;
  }

  int sock = ConnectUnixSocket(cfg.socket_path);
  if (sock < 0) {
    std::cerr << "firestore_sync: failed to connect to socket\n";
    return 1;
  }

  auto connection =
      google::cloud::firestore::MakeFirestoreConnection(cfg.project_id);
  google::cloud::firestore::FirestoreClient client(connection);
  auto doc = DocRef(client, cfg.project_id, cfg.device_ble_mac);

  try {
    if (!DocumentExists(client, doc)) {
      std::string line;
      nlohmann::json initial_state;
      for (;;) {
        char buffer[4096];
        ssize_t n = ::read(sock, buffer, sizeof(buffer));
        if (n <= 0)
          break;
        line.append(buffer, buffer + n);
        auto pos = line.find('\n');
        if (pos == std::string::npos)
          continue;
        std::string one = line.substr(0, pos);
        line.erase(0, pos + 1);
        if (one.empty())
          continue;
        auto msg = nlohmann::json::parse(one, nullptr, false);
        if (!msg.is_object())
          continue;
        auto kind_it = msg.find("kind");
        auto payload_it = msg.find("payload");
        if (kind_it == msg.end() || payload_it == msg.end())
          continue;
        if (*kind_it == "initial_state" && payload_it->is_object()) {
          initial_state = *payload_it;
          break;
        }
      }
      if (!initial_state.is_null()) {
        WriteInitialState(client, doc, initial_state);
      }
    }
  } catch (...) {
  }

  std::thread listener_thread(RunListener, client, cfg.project_id,
                              cfg.device_ble_mac, sock);

  std::string buf;
  while (true) {
    char buffer[4096];
    ssize_t n = ::read(sock, buffer, sizeof(buffer));
    if (n <= 0)
      break;
    buf.append(buffer, buffer + n);
    std::size_t pos;
    while ((pos = buf.find('\n')) != std::string::npos) {
      std::string line = buf.substr(0, pos);
      buf.erase(0, pos + 1);
      if (line.empty())
        continue;
      auto msg = nlohmann::json::parse(line, nullptr, false);
      if (!msg.is_object())
        continue;
      auto kind_it = msg.find("kind");
      auto payload_it = msg.find("payload");
      if (kind_it == msg.end() || payload_it == msg.end())
        continue;
      if (*kind_it == "device_state" && payload_it->is_object()) {
        ApplyDeviceStateUpdate(client, doc, *payload_it);
      }
    }
  }

  ::close(sock);
  if (listener_thread.joinable())
    listener_thread.join();
  return 0;
}
