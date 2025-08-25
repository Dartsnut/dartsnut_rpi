import pygame
import sys
import dartsnut
import numpy as np
from PIL import Image
import signal
import math
import time


# Initialize pygame
pygame.init()
# Set up display
WIDTH, HEIGHT = 128, 128
screen = pygame.display.set_mode((128, 160), pygame.SRCALPHA)
# Surfaces
dart_board_surface = pygame.Surface((128,128), pygame.SRCALPHA)
dart_board_animate_surface = pygame.Surface((128,128), pygame.SRCALPHA)
score_board_surface = pygame.Surface((64,32), pygame.SRCALPHA)
#images
score_board_red = pygame.image.load("Scoreboard Player Red.png").convert()
score_board_blue = pygame.image.load("Scoreboard Player Blue.png").convert()
# Set up clock for controlling frame rate
clock = pygame.time.Clock()
FPS = 60
ANIMATE_DURATION = 100  # milliseconds
# shots
old_shots = []
old_shots_count = []
shots = []
player = 0 # player color 0 = blue, 1 = red
player_scores = [501, 501] # player scores
#sectors for the dartboard
sectors = []
scores = [[0 for _ in range(128)] for _ in range(128)]

# draw the dart board
def init_dartboard_sectors():
    # radius
    radius_double_bull = 3
    radius_single_bull = 7
    radius_inner_triple = 25
    radius_outer_triple = 31
    radius_inner_double = 50
    radius_outer_double = 56

    # scores
    SCORES = [6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5, 20, 1, 18, 4, 13]

    #text position
    TEXT_CORNER = [
        (80,2), # 1
        (97, 110), # 2
        (62, 120), # 3
        (111, 25), # 4
        (43,2), # 5
        (121, 59), # 6
        (26, 110), # 7
        (4,79), # 8
        (12,25), # 9
        (118,79), (123,79),# 10
        (0,60), (4,60), # 11
        (21,10), (26,10), # 12
        (118,41), (123,41), # 13
        (0,41), (5,41), # 14
        (111,96), (116,96), # 15
        (8,96), (13,96), # 16
        (77,118), (82,118), # 17
        (96,10), (101,10),  # 18
        (41,118), (46,118),  # 19
        (59,0), (65,0)  # 20
    ]

    #sector colors
    SECTOR_COLORS = [
        (150,41,3),
        (3,124,32),
        (247,245,119),
        (0,0,0)
    ]

    #texts for the dartboard
    font = pygame.font.Font(None, 15)
    for i in range(20):
        if (i < 9):
            text_surface = font.render(str(i+1), False, (255,255,255), pygame.SRCALPHA)
            dart_board_surface.blit(text_surface, TEXT_CORNER[i])
        else:
            text_surface_tens = font.render(str((i+1) // 10), False, (255,255,255), pygame.SRCALPHA)
            dart_board_surface.blit(text_surface_tens, TEXT_CORNER[i*2 - 9])
            text_surface_units = font.render(str((i+1) % 10), False, (255,255,255), pygame.SRCALPHA)
            dart_board_surface.blit(text_surface_units, TEXT_CORNER[i*2 - 8])

    center = (WIDTH // 2, HEIGHT // 2)
    # pixels in double bull
    sectors.append([])
    for x in range(center[0] - radius_double_bull, center[0] + radius_double_bull + 1):
        for y in range(center[1] - radius_double_bull, center[1] + radius_double_bull + 1):
            if math.sqrt((x - center[0])**2 + (y - center[1])**2) < radius_double_bull:
                sectors[0].append((x, y))
                dart_board_surface.set_at((x, y), SECTOR_COLORS[0])
                scores[x][y] = 50

    # pixels in single bull
    sectors.append([])
    for x in range(center[0] - radius_single_bull, center[0] + radius_single_bull + 1):
        for y in range(center[1] - radius_single_bull, center[1] + radius_single_bull + 1):
            distance = math.sqrt((x - center[0])**2 + (y - center[1])**2)
            if radius_double_bull <= distance < radius_single_bull:
                sectors[1].append((x, y))
                dart_board_surface.set_at((x, y), SECTOR_COLORS[1])
                scores[x][y] = 25

    # Divide the dartboard into 20 sectors (18° each)
    for i in range(20):
        start_angle = math.radians(i * 18 - 9)
        end_angle = math.radians((i + 1) * 18 - 9)
        sector_pixels = []
        color = (0,0,0)
        if (i // 4) % 2 == 0:
            if i % 2 == 0:
                color = SECTOR_COLORS[2]
            else:
                color = SECTOR_COLORS[1]
        else:
            if i % 2 == 1:
                color = SECTOR_COLORS[0]
            else:
                color = SECTOR_COLORS[3]
        for x in range(center[0] - radius_outer_double, center[0] + radius_outer_double + 1):
            for y in range(center[1] - radius_outer_double, center[1] + radius_outer_double + 1):
                distance = math.sqrt((x - center[0])**2 + (y - center[1])**2)
                if radius_single_bull <= distance < radius_outer_double:
                    angle = math.atan2(y - center[1], x - center[0])
                    if (i == 0): 
                        if start_angle <= angle < end_angle:
                            sector_pixels.append((x, y))
                    else:
                        if angle < 0:
                            angle += 2 * math.pi
                        if start_angle <= angle < end_angle:
                            sector_pixels.append((x, y))
        # Divide the sector into 4 parts
        small_sector = [[],[],[],[]]
        for x, y in sector_pixels:
            distance = math.sqrt((x - center[0])**2 + (y - center[1])**2)
            if radius_single_bull <= distance < radius_inner_triple:
                small_sector[0].append((x, y))  # Inner single
                scores[x][y] = SCORES[i]
                if (i % 2 == 1):
                    dart_board_surface.set_at((x, y), SECTOR_COLORS[2])
                else:
                    dart_board_surface.set_at((x, y), SECTOR_COLORS[3])
            elif radius_inner_triple <= distance < radius_outer_triple:
                small_sector[1].append((x, y))  # Triple ring
                scores[x][y] = SCORES[i]*3
                if (i % 2 == 1):
                    dart_board_surface.set_at((x, y), SECTOR_COLORS[0])
                else:
                    dart_board_surface.set_at((x, y), SECTOR_COLORS[1])
            elif radius_outer_triple <= distance < radius_inner_double:
                small_sector[2].append((x, y))  # Outer single
                scores[x][y] = SCORES[i]
                if (i % 2 == 1):
                    dart_board_surface.set_at((x, y), SECTOR_COLORS[2])
                else:
                    dart_board_surface.set_at((x, y), SECTOR_COLORS[3])
            elif radius_inner_double <= distance < radius_outer_double:
                small_sector[3].append((x, y))  # Double ring
                scores[x][y] = SCORES[i]*2
                if (i % 2 == 1):
                    dart_board_surface.set_at((x, y), SECTOR_COLORS[0])
                else:
                    dart_board_surface.set_at((x, y), SECTOR_COLORS[1])
        sectors.append(small_sector[0])
        sectors.append(small_sector[1])
        sectors.append(small_sector[2])
        sectors.append(small_sector[3])

def update_frame_buffer():
    # Blit the surfaces
    screen.fill((0,0,0))
    screen.blits(((dart_board_surface, (0, 0)), (score_board_surface, (0, 128)), (dart_board_animate_surface, (0, 0))))
    # Update the display
    pygame.display.flip()
    # Get the pixel data from the screen
    dartsnut.update_frame_buffer(np.transpose(pygame.surfarray.array3d(screen), (1,0,2)))

def get_darts_hits():
    # Get the dart hits from the shared memory
    hits = []
    darts = dartsnut.get_darts()
    for i in range(len(darts)):
        if (i >= len(old_shots)):
            old_shots.append([-1,-1])
            old_shots_count.append(0)
        x = darts[i][0]
        y = darts[i][1]
        if (x >= 0):
            old_shots_count[i] += 1
            if (old_shots_count[i] >= 5):
                old_shots_count[i] = 5
                if ((old_shots[i][0] == -1) & (old_shots[i][1] == -1)):
                    old_shots[i][0] = x
                    old_shots[i][1] = y
                    hits.append([x,y])
        else:
            old_shots[i][0] = -1
            old_shots[i][1] = -1
            old_shots_count[i] = 0
    return hits

def get_buttons():
    # Get the buttons from the shared memory
    buttons = dartsnut.get_buttons()
    # Decode the buttons into individual bits
    button_states = [
        bool(buttons & 0b00000001),
        bool(buttons & 0b00000010),
        bool(buttons & 0b00000100),
        bool(buttons & 0b00001000),
        bool(buttons & 0b00010000),
        bool(buttons & 0b00100000),
        bool(buttons & 0b01000000),
        bool(buttons & 0b10000000)
    ]
    button_pressed = [False for _ in range(8)]
    for i in range(8):
        if (button_states[i] != get_buttons.old_buttons[i]):
            get_buttons.old_buttons[i] = button_states[i]
            if (button_states[i]):
                button_pressed[i] = True
    return button_pressed
get_buttons.old_buttons = [False for _ in range(8)]

def hit_sector_animate(hit, duration): #hit stand for the sector
    factor = 2
    if ((hit % 4 == 2) | (hit % 4 == 0)):
        factor = 1
    elif (hit % 4 == 3):
        factor = 3
    # Change the color of the sector
    if (hit >= 2):
        for _ in range(factor):  # Blink 3 times
            for sector in range(((hit - 2) // 4) * 4 + 2, ((hit - 2) // 4) * 4 + 6):
                for pixel in sectors[sector]:
                    dart_board_animate_surface.set_at(pixel, (255, 255, 255))  # Set to white for blinking
            update_frame_buffer()
            pygame.time.delay(duration)  # Delay for visibility
            dart_board_animate_surface.fill((0, 0, 0, 0))
            update_frame_buffer()
            pygame.time.delay(duration)  # Delay for visibility
    else:
        for pixel in sectors[0]:
            dart_board_animate_surface.set_at(pixel, (255, 255, 255))
        update_frame_buffer()
        pygame.time.delay(duration)  # Delay for visibility
        dart_board_animate_surface.fill((0, 0, 0, 0))
        for pixel in sectors[1]:
            dart_board_animate_surface.set_at(pixel, (255, 255, 255))
        update_frame_buffer()
        pygame.time.delay(duration)  # Delay for visibility
        dart_board_animate_surface.fill((0, 0, 0, 0))
        for sector in range(20):
            for pixel in sectors[sector*4+2]:
                dart_board_animate_surface.set_at(pixel, (255, 255, 255))
        update_frame_buffer()
        pygame.time.delay(duration)  # Delay for visibility
        dart_board_animate_surface.fill((0, 0, 0, 0))
        for sector in range(20):
            for pixel in sectors[sector*4+3]:
                dart_board_animate_surface.set_at(pixel, (255, 255, 255))
        update_frame_buffer()
        pygame.time.delay(duration)  # Delay for visibility
        dart_board_animate_surface.fill((0, 0, 0, 0))
        for sector in range(20):
            for pixel in sectors[sector*4+4]:
                dart_board_animate_surface.set_at(pixel, (255, 255, 255))
        update_frame_buffer()
        pygame.time.delay(duration)  # Delay for visibility
        dart_board_animate_surface.fill((0, 0, 0, 0))
        for sector in range(20):
            for pixel in sectors[sector*4+5]:
                dart_board_animate_surface.set_at(pixel, (255, 255, 255))
        update_frame_buffer()
        pygame.time.delay(duration)  # Delay for visibility
        dart_board_animate_surface.fill((0, 0, 0, 0))
        update_frame_buffer()
        pygame.time.delay(duration)  # Delay for visibility

def switch_player_animate(player):
    if (player):
        colors = [(0, 0, 255), (0, 0, 192), (0, 0, 128), (0, 0, 64)]
    else:
        colors = [(255, 0, 0), (192, 0, 0), (128, 0, 0), (64, 0, 0)]
    for i in range(23):
        for j in range(4):
            index = i - j
            if ((index >= 0) & (index < 20)):
                for k in range(4):
                    for pixel in sectors[index*4 + k + 2]:
                        dart_board_animate_surface.set_at(pixel, colors[j])  # Set to white for blinking
        update_frame_buffer()
        pygame.time.delay(ANIMATE_DURATION // 4)  # Delay for visibility
        dart_board_animate_surface.fill((0, 0, 0, 0))
    update_frame_buffer()


def hit_text_animate(score):
    # Define the animate frames
    FRAMES = 20
    for i in range(FRAMES):
        # Clear the score board surface
        dart_board_animate_surface.fill((0, 0, 0, 0))
        # Create a text surface
        if (score == 50):
            font = pygame.font.Font(None, 60 - (80 // FRAMES) * i)
            text_surface = font.render("D-Bull", True, (255, 255, 255))
        elif (score == 25):
            font = pygame.font.Font(None, 60 - (80 // FRAMES) * i)
            text_surface = font.render("S-Bull", True, (255, 255, 255))
        elif (score == 0):
            font = pygame.font.Font(None, 60 - (100 // FRAMES) * i)
            text_surface = font.render("Miss !", True, (255, 255, 255))
        else:
            font = pygame.font.Font(None, 100 - (100 // FRAMES) * i)
            text_surface = font.render(str(score), True, (255, 255, 255))
        # Get the rectangle of the text surface
        text_rect = text_surface.get_rect(center=(64 - ((64 - 40) // FRAMES * i), 64 + ((128 - 64) // FRAMES * i)))
        # Blit the text surface to the score board surface
        dart_board_animate_surface.blit(text_surface, text_rect)
        # Update the display
        update_frame_buffer()
        # Delay for visibility
        if (i == 0):
            pygame.time.delay(ANIMATE_DURATION * 4)
        else:
            pygame.time.delay(ANIMATE_DURATION // 4)
    # Clear the score board surface
    dart_board_animate_surface.fill((0, 0, 0, 0))
    update_frame_buffer()

def draw_score_board(player, shots):
    score_board_surface.fill((0,0,0,0))
    # Clear the score board surface
    if (player == 0):
        score_board_surface.blit(score_board_red, (0, 0))
    else:
        score_board_surface.blit(score_board_blue, (0, 0))
    # Draw the score
    # For the player
    font = pygame.font.Font(None, 27)
    text_surface = font.render(str(player_scores[player]), False, (255, 255, 255))
    text_rect = text_surface.get_rect(center = (46, 13))
    score_board_surface.blit(text_surface, text_rect)
    # For the opponent
    font = pygame.font.Font(None, 15)
    text_surface = font.render(str(player_scores[1-player]), False, (255, 255, 255))
    text_rect = text_surface.get_rect(center = (48, 28))
    score_board_surface.blit(text_surface, text_rect)
    # For the shots
    for i in range(len(shots)):
        font = pygame.font.Font(None, 15)
        text_surface = font.render(str(shots[i]), False, (255, 255, 255))
        if (i == 0):
            text_rect = text_surface.get_rect(center = (16, 7))
        elif (i == 1):
            text_rect = text_surface.get_rect(center = (16, 17))
        else:
            text_rect = text_surface.get_rect(center = (16, 27))
        score_board_surface.blit(text_surface, text_rect)
    pygame.time.delay(50)
    update_frame_buffer()

# Draw the Dart board
init_dartboard_sectors()
# Draw the score board
draw_score_board(player, shots)
# Update the display
update_frame_buffer()

# Keep the window open until the user closes it
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

    # Get the dart hits
    if (len(shots) < 3):
        hits = get_darts_hits()
        if (len(hits) > 0):
            score = scores[hits[0][0]][hits[0][1]]
            if (score > 0):
                # Check if the dart hit a sector
                for j in range(len(sectors)):
                    if (hits[0][0], hits[0][1]) in sectors[j]:
                        hit_sector_animate(j, ANIMATE_DURATION // 2)
            # Show score animate
            hit_text_animate(score)
            shots.append(score)
            player_scores[player] -= score
            # Update the score board
            draw_score_board(player, shots)
    
    buttons = get_buttons()
    if (buttons[0]): # A button is pressed
        player = 1- player
        shots = []
        draw_score_board(player, shots)
        # Show the turn animate
        switch_player_animate(player)
    if (buttons[1]):
        player = 0 # player color 0 = blue, 1 = red
        shots = []
        player_scores = [501, 501] # player scores
        draw_score_board(player, shots)
        
    clock.tick(FPS)
    update_frame_buffer()

pygame.quit()
