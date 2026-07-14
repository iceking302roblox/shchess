SHCHESS+ Terminal Chess
SHCHESS+ is a powerful terminal-based chess application with advanced analysis features, database exploration, and engine integration.

🚀 Features
Core Chess
Full chess rules with move validation

PGN support - load, save, and analyze games

FEN support - import/export positions

Board navigation - move forward/backward through games

Color terminal display with board flipping

Analysis Engine
Stockfish integration with direct UCI protocol

Real-time analysis with progress bars

Speed display in kN/s (kilo-nodes per second)

Depth control for analysis depth

MultiPV analysis support

Database Explorer
SQLite3 database for games, puzzles, and endgames

Puzzle of the day from database

Endgame position loading with navigation

Tablebase querying via Lichess/Syzygy APIs

Board Editor
Curses-based visual board editor

Piece placement with keyboard

Position evaluation with engine

FEN import/export

Undo/Redo support

Game Management
PGN file typing with animation

Best game of the day from database

Macbeth analysis with per-move accuracy

Command history with up/down arrow support

🖥️ System Requirements
Minimum
Python 3.9+

Stockfish engine

Terminal with ANSI color support (Linux, macOS, Cygwin, or WSL)

Recommended
4+ CPU cores

8+ GB RAM

SSD storage

Stockfish 16+ (BMI2 optimized)

📥 Installation
1. Install Stockfish
Windows (with Cygwin)
bash
# Download the BMI2 optimized version from official Stockfish releases
wget https://github.com/official-stockfish/Stockfish/releases/latest/download/stockfish-windows-x86-64-bmi2.zip
unzip stockfish-windows-x86-64-bmi2.zip
mv stockfish.exe stockfish.exe
Linux / WSL
bash
sudo apt-get install stockfish
# OR download from: https://stockfishchess.org/download/
macOS
bash
brew install stockfish
2. Install Python Dependencies
bash
pip install python-chess requests
3. Install Windows Curses (Windows only)
bash
pip install windows-curses
4. Clone SHCHESS+
bash
git clone https://github.com/yourusername/shchess
cd shchess
🎮 Usage
Basic Commands
text
analyze [depth]     - Analyze current position
board               - Show the chess board
best                - Find best move
clear               - Clear terminal
db                  - Enter database explorer
fen                 - Show FEN string
flip                - Flip board perspective
help                - Show help
legal               - Show legal moves
quit/exit           - Exit program
reset               - Reset to starting position
undo                - Undo last move
Move Commands
text
e2e4                - Make a move (UCI notation)
Nf3                 - Make a move (SAN notation)
Navigation Commands
text
first               - Go to first move
last                - Go to last move
next                - Next move
previous/prev       - Previous move
Game Management
text
save [file]         - Save game to PGN
load [file]         - Load game from PGN
import [file]       - Import PGN or FEN file
type [file]         - Type PGN file with animation
stop                - Stop typing animation
Analysis Commands
text
analyze [depth]     - Analyze position with progress bar
eval                - Quick evaluation
best                - Find best move
tablebase / tb      - Query online endgame tablebase
Advanced Commands
text
$boardedit          - Launch visual board editor (curses)
db                  - Enter database explorer
macbeth             - Run Macbeth analysis on loaded game
showhistory         - Show game tree with variations
deleteline [idx]    - Delete variation line
addcomment <text>   - Add comment to current position
makemainline [idx]  - Promote variation to mainline
🏃 Quick Start
bash
# Start SHCHESS+
python3 shchess.py

# Make a move
e2e4

# Analyze the position
analyze 20

# Find best move
best

# Flip board
flip

# Show FEN
fen

# Load a game
load game.pgn

# Browse database
db

# Launch board editor
$boardedit
📊 Performance
SHCHESS+ is optimized for maximum engine performance:

Position Type	Speed (kN/s)
Starting Position	1,400-1,500
Complex Middlegame	1,500-2,000
Tactical Positions	2,500-4,800
Forcing Endgames	4,000-4,800
Hardware Testing
CPU: Intel Core i5-4590S @ 3.00GHz (4 cores, 4 threads)

Speed: 1,400-4,800 kN/s depending on position

Depth 25 analysis: 2-30 seconds depending on complexity

🧩 Architecture
text
SHCHESS+
├── ChessTerminal (Main UI)
├── ChessEngine (Subprocess-based Stockfish wrapper)
├── Analyse (Analysis with progress bar)
├── BoardEditorManager (Curses board editor)
├── DatabaseExplorer (SQLite3 database browser)
├── MoveNavigator (Game navigation)
├── FileTyper (PGN typing animation)
└── ConfigManager (Configuration management)
⚡ Engine Optimization
SHCHESS+ uses:

Direct UCI protocol (bypassing python-chess engine control)

Subprocess-based communication (avoiding asyncio issues in Cygwin)

Continuous search (single go depth command)

Multi-threading (full CPU utilization)

BMI2 optimization (CPU-specific instruction sets)

🛠️ Configuration
config.json
json
{
  "puzzle_index": 0,
  "puzzle_enabled": true,
  "database_path": "chess_content.db"
}
Database Schema
sql
-- Puzzles table
CREATE TABLE puzzles (
    id INTEGER PRIMARY KEY,
    fen TEXT,
    moves TEXT,
    rating INTEGER,
    themes TEXT
);

-- Games table
CREATE TABLE games (
    id INTEGER PRIMARY KEY,
    pgn TEXT,
    white TEXT,
    black TEXT,
    event TEXT,
    date TEXT
);

-- Endgames table
CREATE TABLE endgames (
    id INTEGER PRIMARY KEY,
    fen TEXT,
    pgn_excerpt TEXT,
    white TEXT,
    black TEXT,
    result TEXT,
    material TEXT
);
📝 Examples
Load and Analyze a Game
bash
load kasparov.pgn
analyze 25
next
prev
first
Board Editor
bash
$boardedit
# Use hjkl to move cursor
# Press KQRBNP to place pieces
# Press . to clear a square
# Press t to toggle turn
# Press q to quit
Database Explorer
bash
db
endgames
load 123
quit
Tablebase Query
bash
tablebase
# Shows WDL, DTZ, and best moves from Lichess/Syzygy
Macbeth Analysis
bash
load game.pgn
macbeth 24
next
prev
