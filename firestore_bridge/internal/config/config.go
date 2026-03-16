package config

// Firebase project configuration and credentials for the bridge.
// TODO: move these secrets to environment variables or a secure store.

const (
	ProjectID = "my-dartsnut"
	// Use named Firestore database "device" instead of the default.
	Database = "device"

	APIKey   = "AIzaSyAiroI1etKgI8WfFR2rdQ5JNpqrVY-llEw"
	Email    = "pi@dartsnut.com"
	Password = "asdfqwer"
)

// DatabaseName returns the full Firestore database resource name.
func DatabaseName() string {
	return "projects/" + ProjectID + "/databases/" + Database
}

