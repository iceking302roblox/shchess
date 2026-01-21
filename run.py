#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SHCHESS+ - PURE TERMINAL VERSION - ANDROID TERMUX COMPATIBLE
NO GUI - NO TKINTER - PURE COMMAND LINE
FULL TERMINAL CHESS WITH ANDROID SUPPORT
"""

import os
import sys
import time
import json
import re
import sqlite3
import threading
import queue
import hashlib
import math
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any, Callable
import io
import copy
import subprocess
import signal
import shlex
import queue
from datetime import datetime
TERM_WIDTH = 64
TERM_HEIGHT = 63
# Try to import curses for board editor
try:
    import curses
    CURSES_AVAILABLE = True
except ImportError:
    CURSES_AVAILABLE = False
    curses = None

# ============================================================
# CHESS IMPORTS - ANDROID TERMUX COMPATIBLE
# ============================================================
CHESS_AVAILABLE = False
try:
    import chess
    import chess.pgn
    import chess.engine
    import chess.svg
    CHESS_AVAILABLE = True
except ImportError as e:
    print("=" * 60)
    print("WARNING: python-chess not installed properly")
    print("=" * 60)
    print("\nFor Android Termux, run these commands:")
    print("  pkg update && pkg upgrade")
    print("  pkg install python python-pip clang")
    print("  pip install --upgrade pip setuptools wheel")
    print("  pip install python-chess")
    print("\nIf you still get errors, try:")
    print("  pip install python-chess --no-cache-dir")
    print("  pip install python-chess --no-binary :all:")
    print("\nError details:", str(e))
    print("=" * 60)
    
    # python-chess is REQUIRED - no dummy fallback
    print(f"{TerminalColors.RED}CRITICAL: python-chess is required for this program{TerminalColors.RESET}")
    print(f"{TerminalColors.YELLOW}Please install it and try again.{TerminalColors.RESET}")
    sys.exit(1)

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    requests = None
    REQUESTS_AVAILABLE = False

try:
    from PIL import Image, ImageDraw, ImageFont, ImageSequence
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# ============================================================
# ONLINE TABLEBASE
# ============================================================

class OnlineTablebase:
    """Query online endgame tablebases"""
    
    APIS = {
        "lichess": "https://tablebase.lichess.ovh/standard",
        "syzygy": "https://syzygy-tables.info/api/standard",
        "lokasoft": "http://www.lokasoft.nl/tbapi.aspx",
    }
    
    @staticmethod
    def query(fen: str) -> Dict[str, Any]:
        """Query tablebase for position"""
        if not REQUESTS_AVAILABLE:
            return {"error": "requests library not available. Install with: pip install requests"}
        
        for name in ("lichess", "syzygy", "lokasoft"):
            try:
                if name == "lokasoft":
                    r = requests.get(
                        OnlineTablebase.APIS[name],
                        params={"ProbePosition": fen},
                        timeout=8
                    )
                    r.raise_for_status()
                    return {
                        "source": "lokasoft",
                        "raw": r.text
                    }
                else:
                    r = requests.get(
                        OnlineTablebase.APIS[name],
                        params={"fen": fen},
                        timeout=8
                    )
                    r.raise_for_status()
                    data = r.json()
                    data["_source"] = name
                    return data
            except Exception:
                continue
        return {"error": "All tablebase providers failed"}



# ============================================================
# CONFIGURATION MANAGER
# ============================================================

class ConfigManager:
    """Manages configuration including puzzle progress"""

    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        self.config = self.load_config()

    def load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "puzzle_index": 0,
            "puzzle_enabled": True,
            "database_path": "/data/data/com.termux/files/home/shchess/content/database/chess_content.db"
        }

    def save_config(self):
        try:
            with open(self.config_path, "w") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}")

    def get_puzzle_index(self) -> int:
        return int(self.config.get("puzzle_index", 0))

    def increment_puzzle_index(self):
        self.config["puzzle_index"] = self.get_puzzle_index() + 1
        self.save_config()

    def is_puzzle_enabled(self) -> bool:
        return bool(self.config.get("puzzle_enabled", True))

    def set_puzzle_enabled(self, enabled: bool):
        self.config["puzzle_enabled"] = bool(enabled)
        self.save_config()

    def get_database_path(self) -> str:
        return str(self.config.get("database_path", "chess_content.db"))

class BestGame:
    """
    Rotating 'Best Game' loader from games table.
    """

    def __init__(self, terminal, config: ConfigManager):
        self.terminal = terminal
        self.config = config
        self.db_path = config.get_database_path()

        if "bestgame_index" not in self.config.config:
            self.config.config["bestgame_index"] = 0
            self.config.save_config()

    def load_next(self):
        if not os.path.exists(self.db_path):
            return False, "Database not found"

        idx = int(self.config.config.get("bestgame_index", 0))

        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()

            cur.execute("""
                SELECT id, pgn, white, black, event, date
                FROM games
                ORDER BY id
                LIMIT 1 OFFSET ?
            """, (idx,))

            row = cur.fetchone()
            conn.close()

            if not row:
                return False, "No more games"

            gid, pgn, white, black, event, date = row

            game = chess.pgn.read_game(io.StringIO(pgn))
            if not game:
                return False, "Invalid PGN"

            self.terminal.load_game(game)

            self.config.config["bestgame_index"] = idx + 1
            self.config.save_config()

            print(
                f"{TerminalColors.GREEN}Best Game of the Day{TerminalColors.RESET}\n"
                f"{white} vs {black}\n"
                f"{event} ({date})\n"
            )

            return True, "Game loaded"

        except Exception as e:
            return False, str(e)

# ============================================================
# PUZZLE MANAGER
# ============================================================

class PuzzleManager:
    """
    Backward-compatible PuzzleManager.
    Accepts (terminal) OR (terminal, config).
    """

    def __init__(self, terminal, config=None):
        self.terminal = terminal

        # Auto-create config if not provided
        if config is None:
            self.config = ConfigManager()
        else:
            self.config = config

        self._impl = ChessPuzzles(self.terminal, self.config)

    def toggle(self, state: bool):
        return self._impl.toggle(state)

    def load_puzzle_of_the_day(self):
        return self._impl.load_puzzle_of_the_day()


class ChessPuzzles:
    """
    Puzzle of the Day manager.
    Zero intrusion – only called from terminal hooks.
    """

    def __init__(self, terminal, config: ConfigManager):
        self.terminal = terminal
        self.config = config
        self.db_path = config.get_database_path()

    def enabled(self) -> bool:
        return self.config.is_puzzle_enabled()

    def toggle(self, state: bool):
        self.config.set_puzzle_enabled(state)

    def load_puzzle_of_the_day(self):
        if not self.enabled():
            return False, "Puzzle disabled"

        if not os.path.exists(self.db_path):
            return False, "Database not found"

        idx = self.config.get_puzzle_index()

        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()

            cur.execute("""
                SELECT id, fen, moves, rating, themes
                FROM puzzles
                ORDER BY id
                LIMIT 1 OFFSET ?
            """, (idx,))

            row = cur.fetchone()
            conn.close()

            if not row:
                return False, "No more puzzles"

            pid, fen, moves, rating, themes = row

            board = chess.Board(fen)

            # reset terminal state cleanly
            self.terminal.board = board
            self.terminal.game = None
            self.terminal.game_node = None
            self.terminal.move_index = 0

            self.config.increment_puzzle_index()

            header = (
                f"{TerminalColors.CYAN}Puzzle of the Day{TerminalColors.RESET}\n"
                f"ID: {pid} | Rating: {rating}\n"
                f"Themes: {themes}\n"
            )
            print(header)

            return True, "Puzzle loaded"

        except Exception as e:
            return False, str(e)



# ============================================================
# ENDGAME MANAGER
# ============================================================

class EndgameManager:
    """Manages endgame positions from database"""

    def __init__(self, database_path: str):
        self.database_path = database_path

    def load_endgame_position(self, position_id: int = None, plies_back: int = 6):
        """Load endgame position from database, going back N plies (default -6)"""
        if not os.path.exists(self.database_path):
            print(f"Database not found: {self.database_path}")
            return None

        try:
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()

            if position_id is None:
                cursor.execute("SELECT id, fen, moves FROM endgames ORDER BY RANDOM() LIMIT 1")
            else:
                cursor.execute("SELECT id, fen, moves FROM endgames WHERE id = ?", (position_id,))

            row = cursor.fetchone()
            conn.close()

            if not row:
                print("No endgame found")
                return None

            endgame_id, final_fen, moves_str = row

            if moves_str and plies_back > 0:
                try:
                    board = self.board 
                    moves = moves_str.split()

                    moves_to_apply = max(0, len(moves) - plies_back)
                    for i in range(moves_to_apply):
                        move = chess.Move.from_uci(moves[i])
                        board.push(move)

                    return board.fen()
                except Exception as e:
                    print(f"Error recounting endgame plies: {e}")
                    return final_fen

            return final_fen

        except Exception as e:
            print(f"Error loading endgame: {e}")
            return None


class Analyse:
    """
    Engine analysis with progress bar and KN/s (Kilonodes per second) display.
    Similar to tqdm progress bar for chess analysis.
    """

    def __init__(self, engine_path: str = "./stockfish"):
        self.engine_path = engine_path
        self.engine = None
        self.stop_flag = threading.Event()
        self.target_depth = 0
        self._last_nodes = 0
        self._last_ts = None

    def start_engine(self):
        try:
            self.engine = chess.engine.SimpleEngine.popen_uci(self.engine_path)
            return True
        except Exception as e:
            print(f"Error starting engine: {e}")
            return False

    def stop_engine(self):
        if self.engine:
            try:
                self.engine.quit()
            except Exception:
                pass
        self.engine = None

    def stop(self):
        self.stop_flag.set()

    def format_kilonodes(self, nodes: int) -> str:
        kn = nodes / 1000.0
        if kn >= 1000:
            return f"{kn/1000:.1f}M"
        return f"{kn:.1f}K"

    def format_time(self, seconds: float) -> str:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"

    def draw_progress_bar(self, depth: int, nodes: int, elapsed: float, bar_width: int = 40):
        progress = min(1.0, depth / self.target_depth) if self.target_depth > 0 else 0
        filled = int(bar_width * progress)
        now = time.time()

        if self._last_ts is None:
            inst_kns = 0.0
        else:
            dt = max(now - self._last_ts, 0.001)
            dn = max(nodes - self._last_nodes, 0)
            inst_kns = (dn / dt) / 1000.0

        avg_kns = (nodes / elapsed) / 1000.0

        self._last_nodes = nodes
        self._last_ts = now
        bar = "█" * filled + "░" * (bar_width - filled)
        percentage = int(progress * 100)

        nodes_str = self.format_kilonodes(nodes)
        time_str = self.format_time(elapsed)

        sys.stdout.write(
            f"\r{TerminalColors.CYAN}"
            f"Depth {depth}/{self.target_depth} |{bar}| "
            f"{percentage}% | "
            f"{avg_kns:6.1f} kN/s avg | "
            f"{time_str}"
            f"{TerminalColors.RESET}"
        )
        sys.stdout.flush()

    def analyse_position(self, board: chess.Board, depth: int = 20, multipv: int = 1):
        if not self.engine:
            if not self.start_engine():
                print("Engine not available")
                return []

        self.stop_flag.clear()
        self.target_depth = depth
        start = time.time()

        results = []
        last_nodes = 0
        last_depth = 0
        last_info = None

        try:
            limit = chess.engine.Limit(depth=depth)
            with self.engine.analysis(board, limit, info=chess.engine.INFO_ALL, multipv=multipv) as analysis:
                for info in analysis:
                    if self.stop_flag.is_set():
                        break

                    d = info.get("depth", last_depth)
                    n = info.get("nodes", last_nodes)

                    last_depth = d
                    last_nodes = n
                    last_info = info

                    elapsed = max(time.time() - start, 0.001)
                    self.draw_progress_bar(d, n, elapsed)

            # newline after progress bar
            print()

            # Display best moves
            if last_info:
                print(f"\n{TerminalColors.GREEN}Analysis complete!{TerminalColors.RESET}")
                
                # Handle MultiPV results
                if multipv > 1 and isinstance(last_info, list):
                    for i, pv_info in enumerate(last_info[:multipv], 1):
                        self._display_pv(board, pv_info, i)
                else:
                    self._display_pv(board, last_info, 1)
                
                return [last_info] if last_info else []
            else:
                print(f"{TerminalColors.YELLOW}No analysis results{TerminalColors.RESET}")
                return []

        except Exception as e:
            print(f"\n{TerminalColors.RED}Analysis error: {e}{TerminalColors.RESET}")
            return []
    
    def _display_pv(self, board: chess.Board, info: dict, line_num: int = 1):
        """Display a single principal variation"""
        try:
            score = info.get("score")
            pv = info.get("pv", [])
            
            if score and pv:
                # Format score
                if score.is_mate():
                    mate_in = score.relative.mate()
                    score_str = f"M{mate_in}"
                else:
                    cp = score.relative.score()
                    score_str = f"{cp/100.0:+.2f}"
                
                # Format moves (first 5)
                temp_board = board.copy()
                move_strs = []
                for i, move in enumerate(pv[:5]):
                    if i >= 5:
                        break
                    move_strs.append(temp_board.san(move))
                    temp_board.push(move)
                
                pv_str = " ".join(move_strs)
                if len(pv) > 5:
                    pv_str += " ..."
                
                # Display
                print(f"{TerminalColors.YELLOW}Line {line_num}:{TerminalColors.RESET} {TerminalColors.BRIGHT_WHITE}{score_str}{TerminalColors.RESET} - {pv_str}")
        except Exception as e:
            print(f"Error displaying PV: {e}")
    
    def analyze(self, board: chess.Board, depth: int = 20):
        """Simple analyze method - shorthand for analyse_position"""
        return self.analyse_position(board, depth=depth, multipv=1)



# ============================================================
# BOARD EDITOR (CURSES-BASED)
# ============================================================

# Piece symbols for setting pieces
PIECE_KEYS = "pnbrqkPNBRQK"

# Helper function
def clamp(val, minimum, maximum):
    return max(minimum, min(val, maximum))

# Help line for board editor
HELP_LINE = "hjkl=move | PNBRQK=set piece | .=clear | t=turn | u/U=undo/redo | r=reset | x=clear | s=save | l=load | f=fen | v=validate | e=eval | a=analysis | q=quit"

import os
import time
import queue
import threading
from datetime import datetime
from typing import List, Dict, Optional

import chess
import chess.engine


class EngineAnalyzer:
    """
    Terminal-only background chess engine analyzer.

    - Crash-safe
    - Board-immutable
    - Verbose
    - ASCII progress bar (tqdm-like)
    - 64-column safe output
    """

    BAR_WIDTH = 24   # fits 64 columns nicely
    KNPS_BAR_WIDTH = 16

    def __init__(
        self,
        engine_path: str,
        analysis_time: float = 1.0,
        multipv: int = 3,
        depth: int = 20,
        verbose: bool = True,
    ):
        self.engine_path = engine_path
        self.analysis_time = analysis_time
        self.multipv = multipv
        self.depth = depth
        self.verbose = verbose

        self.engine: Optional[chess.engine.SimpleEngine] = None
        self.running = False
        self.continuous = False

        self.analysis_queue = queue.Queue(maxsize=1)
        self.result_queue = queue.Queue(maxsize=1)

        self.thread: Optional[threading.Thread] = None
        self.last_fen: Optional[str] = None

    # ---------------------------------------------------------
    # ENGINE LIFECYCLE
    # ---------------------------------------------------------
    def _format_knps_bar(self, knps: float) -> str:
        knps = max(0.0, min(knps, 2000.0))

        ratio = knps / 2000.0
        filled = int(ratio * self.KNPS_BAR_WIDTH)

        bar = "#" * filled + "-" * (self.KNPS_BAR_WIDTH - filled)
        return f"[{bar}] {knps:6.1f} kN/s"


    def start(self) -> bool:
        if not os.path.exists(self.engine_path):
            self._log("ENGINE NOT FOUND")
            return False

        try:
            self.engine = chess.engine.SimpleEngine.popen_uci(self.engine_path)
            self.running = True
            self.thread = threading.Thread(
                target=self._analysis_loop,
                daemon=True
            )
            self.thread.start()
            self._log("ENGINE STARTED")
            return True
        except Exception as e:
            self._log(f"ENGINE INIT FAILED: {e}")
            return False

    def stop(self):
        self.running = False
        self.continuous = False
        if self.engine:
            try:
                self.engine.quit()
            except Exception:
                pass
        self._log("ENGINE STOPPED")

    # ---------------------------------------------------------
    # PUBLIC API
    # ---------------------------------------------------------
    def request_analysis(self, board: chess.Board):
        if not self.running:
            return
        if self.analysis_queue.full():
            return

        # 🔒 snapshot FEN only
        self.analysis_queue.put(board.fen())

    def toggle_continuous(self) -> bool:
        self.continuous = not self.continuous
        self._log(f"CONTINUOUS: {self.continuous}")
        return self.continuous

    def get_results(self) -> List[Dict]:
        results = []
        while not self.result_queue.empty():
            try:
                results = self.result_queue.get_nowait()
            except queue.Empty:
                break
        return results

    # ---------------------------------------------------------
    # CORE LOOP
    # ---------------------------------------------------------
    def _analysis_loop(self):
        while self.running:
            try:
                fen = self._get_next_fen()
                if not fen:
                    time.sleep(0.05)
                    continue

                board = chess.Board(fen)
                root_board = board.copy(stack=False)

                self._progress_start()


                limit = chess.engine.Limit(
                    depth=self.depth,
                    time=self.analysis_time
                )
                
                start_ts = time.time()

                info = self.engine.analyse(
                    root_board,
                    limit,
                    multipv=self.multipv
                )
                
                elapsed = max(time.time() - start_ts, 0.001)


                self._progress_end()

                results = self._format_info(info, root_board, elapsed)

                self._push_results(results)

            except Exception as e:
                self._log(f"ANALYSIS ERROR: {e}")
                time.sleep(0.1)

    # ---------------------------------------------------------
    # HELPERS
    # ---------------------------------------------------------
    def _get_next_fen(self) -> Optional[str]:
        if self.continuous:
            try:
                fen = self.analysis_queue.get(timeout=0.1)
                self.last_fen = fen
                return fen
            except queue.Empty:
                return self.last_fen
        else:
            try:
                fen = self.analysis_queue.get(timeout=0.5)
                self.last_fen = fen
                return fen
            except queue.Empty:
                return None

    def _push_results(self, results: List[Dict]):
        if not self.result_queue.empty():
            try:
                self.result_queue.get_nowait()
            except queue.Empty:
                pass
        self.result_queue.put(results)

    # ---------------------------------------------------------
    # FORMATTING
    # ---------------------------------------------------------
    def _format_info(
        self,
        info_list,
        root_board: chess.Board,
        elapsed: float
    ) -> List[Dict]:
        """
        Format engine info safely with kN/s meter.
        elapsed = wall-clock seconds spent analyzing
        """

        results = []
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")

        # Prevent division explosions
        elapsed = max(elapsed, 0.001)

        for idx, info in enumerate(info_list):
                # -------------------------------------------------
                # SCORE (ABSOLUTELY SAFE)
                # -------------------------------------------------
                score_str = "N/A"
                cp_score = None

                score_obj = info.get("score")
                if score_obj is not None:
                        try:
                                pov = score_obj.pov(root_board.turn)
                                if pov.is_mate():
                                        mate = pov.mate()
                                        if mate is not None:
                                                score_str = f"M{abs(mate)}"
                                else:
                                        cp = pov.score(mate_score=100000)
                                        if cp is not None:
                                                cp_score = cp / 100.0
                                                score_str = f"{cp_score:+.2f}"
                        except Exception:
                                pass  # NEVER crash here

                # -------------------------------------------------
                # PV (LEGALITY GUARANTEED)
                # -------------------------------------------------
                pv_san: List[str] = []
                pv = info.get("pv") or []

                pv_board = root_board.copy(stack=False)

                for mv in pv[:8]:
                        if mv not in pv_board.legal_moves:
                                break
                        try:
                                san = pv_board.san(mv)
                        except Exception:
                                break
                        pv_san.append(san)
                        pv_board.push(mv)

                # -------------------------------------------------
                # NODES + KILONODES / SEC
                # -------------------------------------------------
                nodes = info.get("nodes", 0) or 0

                knps = 0.0
                if nodes > 0:
                        knps = (nodes / elapsed) / 1000.0

                # Clamp for display sanity
                knps = max(0.0, min(knps, 9999.9))

                # -------------------------------------------------
                # RESULT PACKET
                # -------------------------------------------------
                results.append({
                        "rank": idx + 1,
                        "score": score_str,
                        "cp": cp_score,
                        "best": pv_san[0] if pv_san else None,
                        "pv": " ".join(pv_san),
                        "depth": info.get("depth", self.depth),
                        "nodes": nodes,
                        "knps": round(knps, 1),
                        "knps_bar": self._format_knps_bar(knps),
                        "time": timestamp,
                })

        return results

    # ---------------------------------------------------------
    # PROGRESS BAR (TQDM STYLE)
    # ---------------------------------------------------------
    def _progress_start(self):
        if not self.verbose:
            return

        for i in range(self.BAR_WIDTH + 1):
            filled = int((i / self.BAR_WIDTH) * self.BAR_WIDTH)
            bar = "#" * filled + "-" * (self.BAR_WIDTH - filled)
            msg = f"[{bar}] ANALYZING"
            print(msg[:64], end="\r", flush=True)
            time.sleep(self.analysis_time / (self.BAR_WIDTH * 1.5))

    def _progress_end(self):
        if not self.verbose:
            return
        bar = "#" * self.BAR_WIDTH
        print(f"[{bar}] DONE".ljust(64))

    # ---------------------------------------------------------
    # LOGGING
    # ---------------------------------------------------------
    def _log(self, msg: str):
        if not self.verbose:
            return
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line[:64])



# ============================================================
# STOCKFISH ENGINE WRAPPER
# ============================================================

class StockfishEngine:
    """Simple Stockfish engine wrapper"""
    
    def __init__(self, engine_path="./stockfish"):
        self.engine_path = engine_path
        self.engine = None
        self.initialized = False
    
    def initialize(self):
        """Initialize the engine"""
        if self.initialized:
            return True
        
        try:
            if not os.path.exists(self.engine_path):
                return False
            
            self.engine = chess.engine.SimpleEngine.popen_uci(self.engine_path)
            self.initialized = True
            return True
        except Exception as e:
            print(f"Engine init error: {e}")
            return False
    
    def close(self):
        """Close the engine"""
        if self.engine:
            try:
                self.engine.quit()
            except Exception:
                pass
            self.engine = None
            self.initialized = False
    
    def analyze(self, board, depth=20):
        """Analyze a position"""
        if not self.initialized:
            if not self.initialize():
                return None
        
        try:
            limit = chess.engine.Limit(depth=depth)
            info = self.engine.analyse(board, limit)
            return info
        except Exception as e:
            print(f"Analysis error: {e}")
            return None

class BoardEditorManager:
    """
    DROP-IN Board Editor Manager
    - curses based
    - engine-safe
    - NO argparse
    - NO globals
    - NO __main__
    - NO move errors
    """

    PIECE_KEYS = "KQRBNPkqrbnp"

    HELP_LINE = (
        "hjkl/arrows=move | KQRBNP/kqrbnp=set | .=clear | "
        "t=turn | u/U=undo/redo | r=reset | x=clear | "
        "l=load fen | s=save fen | f=show fen | "
        "e=eval | q=quit (auto-analyzing)"
    )

    def __init__(self, terminal, board=None, engine_path="./stockfish"):
        self.terminal = terminal
        self.board = board.copy() if board else chess.Board()
        self.engine_path = engine_path

        self.cursor_sq = chess.E4
        self.msg = ""

        self.undo_stack = []
        self.redo_stack = []

        self.analyzer = None
        self.analysis_results = []
        self.show_analysis = True
        
        self.analysis_thread = None
        self.analysis_running = False
        self.last_fen = None
        self.analysis_lock = threading.Lock()
        self.best_move = None
        self.best_score = None
        self.analysis_info = "Starting engine..."

    # ======================================================
    # PUBLIC ENTRY
    # ======================================================

    def run(self):
        if not CURSES_AVAILABLE:
            print("curses not available")
            return

        curses.wrapper(self._curses_main)

    # ======================================================
    # CURSES CORE
    # ======================================================

    def _curses_main(self, stdscr):
        self.stdscr = stdscr
        curses.curs_set(0)
        stdscr.keypad(True)
        curses.start_color()
        curses.use_default_colors()

        self._init_colors()
        self._init_engine()

        self._splash()

        try:
            self._loop()
        finally:
            self._shutdown()

    def _init_colors(self):
        curses.init_pair(1, curses.COLOR_WHITE, -1)
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(3, curses.COLOR_CYAN, -1)
        curses.init_pair(4, curses.COLOR_YELLOW, -1)
        curses.init_pair(5, curses.COLOR_GREEN, -1)
        curses.init_pair(6, curses.COLOR_RED, -1)
        curses.init_pair(7, curses.COLOR_MAGENTA, -1)

        self.A_NORMAL = curses.color_pair(1)
        self.A_STATUS = curses.color_pair(2)
        self.A_TITLE = curses.color_pair(3) | curses.A_BOLD
        self.A_MSG = curses.color_pair(4) | curses.A_BOLD
        self.A_HL = curses.color_pair(5) | curses.A_BOLD
        self.A_BAD = curses.color_pair(6) | curses.A_BOLD
        self.A_ANALYSIS = curses.color_pair(7) | curses.A_BOLD

    # ======================================================
    # ENGINE
    # ======================================================

    def _init_engine(self):
        if not os.path.exists(self.engine_path):
            self.analysis_info = "Engine not found"
            return

        try:
            self.analyzer = chess.engine.SimpleEngine.popen_uci(self.engine_path)
            self.analysis_running = True
            self.analysis_thread = threading.Thread(
                target=self._analysis_loop,
                daemon=True
            )
            self.analysis_thread.start()
            self.analysis_info = "Analyzing..."
        except Exception as e:
            self.analyzer = None
            self.analysis_info = f"Engine error: {str(e)[:30]}"

    def _analysis_loop(self):
        """Continuous analysis that updates as board changes"""
        while self.analysis_running:
            try:
                fen = self.board.fen()

                # Skip if position hasn't changed
                if fen == self.last_fen:
                    time.sleep(0.05)  # Small delay
                    continue

                self.last_fen = fen
                board_copy = chess.Board(fen)

                # Use progressive depth for responsive updates
                for depth in [12, 18, 22]:
                    if not self.analysis_running or fen != self.board.fen():
                        break  # Position changed, restart
                    
                    try:
                        info = self.analyzer.analyse(
                            board_copy,
                            chess.engine.Limit(depth=depth),
                            multipv=1
                        )

                        score = info.get("score")
                        pv = info.get("pv", [])

                        with self.analysis_lock:
                            # Format score
                            if score:
                                pov = score.pov(board_copy.turn)
                                if pov.is_mate():
                                    mate_num = pov.mate()
                                    score_str = f"M{abs(mate_num)}" if mate_num > 0 else f"M-{abs(mate_num)}"
                                else:
                                    cp = pov.score()
                                    if cp is not None:
                                        score_str = f"{cp/100:+.2f}"
                                    else:
                                        score_str = "?"
                            else:
                                score_str = "?"

                            # Format best move
                            self.best_move = None
                            move_str = "None"
                            if pv and len(pv) > 0:
                                move = pv[0]
                                try:
                                    san_move = board_copy.san(move)
                                    move_str = san_move
                                    self.best_move = move
                                except Exception:
                                    try:
                                        move_str = move.uci()
                                        self.best_move = move
                                    except Exception:
                                        move_str = "?"

                            # Update display with current depth
                            self.analysis_info = f"Depth {depth}: {move_str} [{score_str}]"

                    except Exception as e:
                        with self.analysis_lock:
                            self.analysis_info = f"Analysis error at depth {depth}"
                        break

            except Exception as e:
                time.sleep(0.1)

    # ======================================================
    # LOOP
    # ======================================================

    def _loop(self):
        while True:
            self._draw()

            k = self.stdscr.getch()

            if k in (ord('q'), 27):
                break
            elif k in (curses.KEY_LEFT, ord('h')):
                self._move_cursor(-1, 0)
            elif k in (curses.KEY_RIGHT, ord('l')):
                self._move_cursor(1, 0)
            elif k in (curses.KEY_UP, ord('k')):
                self._move_cursor(0, 1)
            elif k in (curses.KEY_DOWN, ord('j')):
                self._move_cursor(0, -1)
            elif k == ord('u'):
                self._undo()
            elif k == ord('U'):
                self._redo()
            elif k == ord('t'):
                self._toggle_turn()
            elif k == ord('r'):
                self._reset()
            elif k == ord('x'):
                self._clear()
            elif k == ord('f'):
                self.msg = self.board.fen()
            elif k == ord('e'):
                self._engine_eval()
            elif k in (ord('.'), ord(' ')):
                self._set_piece(' ')
            elif 32 <= k <= 126:
                ch = chr(k)
                if ch in self.PIECE_KEYS:
                    self._set_piece(ch)

    # ======================================================
    # BOARD OPS
    # ======================================================

    def _push_undo(self):
        self.undo_stack.append(self.board.fen())
        self.redo_stack.clear()

    def _undo(self):
        if not self.undo_stack:
            self.msg = "Nothing to undo"
            return
        self.redo_stack.append(self.board.fen())
        self.board = chess.Board(self.undo_stack.pop())
        self.msg = "Undo"

    def _redo(self):
        if not self.redo_stack:
            self.msg = "Nothing to redo"
            return
        self.undo_stack.append(self.board.fen())
        self.board = chess.Board(self.redo_stack.pop())
        self.msg = "Redo"

    def _move_cursor(self, dx, dy):
        f = chess.square_file(self.cursor_sq)
        r = chess.square_rank(self.cursor_sq)
        f = max(0, min(7, f + dx))
        r = max(0, min(7, r + dy))
        self.cursor_sq = chess.square(f, r)

    def _set_piece(self, ch):
        self._push_undo()
        if ch in (' ', '.'):
            self.board.remove_piece_at(self.cursor_sq)
        else:
            self.board.set_piece_at(self.cursor_sq, chess.Piece.from_symbol(ch))
        self.msg = "Updated"

    def _toggle_turn(self):
        self._push_undo()
        self.board.turn = not self.board.turn
        self.msg = "Turn toggled"

    def _reset(self):
        self._push_undo()
        self.board.reset()
        self.msg = "Reset"

    def _clear(self):
        self._push_undo()
        self.board.clear()
        self.msg = "Cleared"

    def _engine_eval(self):
        # Manual evaluation trigger
        if self.analyzer:
            try:
                result = self.analyzer.analyse(
                    self.board,
                    chess.engine.Limit(depth=18)
                )
                score = result["score"].white()
                if score.is_mate():
                    self.msg = f"Mate in {score.mate()}"
                else:
                    self.msg = f"Score: {score.score()/100:.2f}"
            except Exception as e:
                self.msg = f"Eval error: {str(e)[:20]}"
        else:
            self.msg = "Engine not available"

    # ======================================================
    # SHUTDOWN
    # ======================================================
    def _shutdown(self):
        self.analysis_running = False

        if self.analysis_thread and self.analysis_thread.is_alive():
            self.analysis_thread.join(timeout=0.5)

        if self.analyzer:
            try:
                self.analyzer.quit()
            except:
                pass

    # ======================================================
    # DRAW
    # ======================================================

    def _draw(self):
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()

        # Title
        title = "BOARD EDITOR"
        self.stdscr.addstr(0, (w - len(title)) // 2, title, self.A_TITLE)

        # Stockfish analysis under title (thread-safe)
        with self.analysis_lock:
            analysis_line = self.analysis_info
        if len(analysis_line) > w - 2:
            analysis_line = analysis_line[:w-2]
        self.stdscr.addstr(1, (w - len(analysis_line)) // 2, analysis_line, self.A_ANALYSIS)

        # Board position
        top = 3
        left = 2

        self.stdscr.addstr(top, left, "  a b c d e f g h")

        for rank in range(7, -1, -1):
            y = top + 1 + (7 - rank)
            self.stdscr.addstr(y, left, f"{rank+1} ")
            x = left + 2
            for file in range(8):
                sq = chess.square(file, rank)
                p = self.board.piece_at(sq)
                ch = p.symbol() if p else '.'
                attr = self.A_HL if sq == self.cursor_sq else self.A_NORMAL
                self.stdscr.addstr(y, x, ch, attr)
                x += 2
            self.stdscr.addstr(y, left + 18, f" {rank+1}")

        self.stdscr.addstr(top + 9, left, "  a b c d e f g h")

        # Help and message lines at bottom
        help_y = min(h - 3, top + 11)
        if help_y > 0:
            self.stdscr.addstr(help_y, 0, self.HELP_LINE[:w - 1])
        
        msg_y = min(h - 2, help_y + 1)
        if msg_y > 0:
            self.stdscr.addstr(msg_y, 0, self.msg[:w - 1], self.A_MSG)

        # Status line at very bottom
        status_y = min(h - 1, msg_y + 1)
        if status_y > 0:
            status = f"Turn: {'White' if self.board.turn else 'Black'} | FEN: {self.board.fen()[:40]}"
            if len(status) > w - 1:
                status = status[:w-1]
            self.stdscr.addstr(status_y, 0, status, self.A_STATUS)

        self.stdscr.refresh()

        self.stdscr.refresh()

    def _splash(self):
        self.stdscr.clear()
        h, w = self.stdscr.getmaxyx()
        text = "Board Editor"
        self.stdscr.addstr(h//2, (w-len(text))//2, text, curses.A_BOLD)
        self.stdscr.refresh()
        time.sleep(1)


# ============================================================
# TERMINAL UTILITIES
# ============================================================

class TerminalColors:
    """ANSI color codes"""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    UNDERLINE = '\033[4m'
    BLINK = '\033[5m'
    REVERSE = '\033[7m'
    HIDDEN = '\033[8m'
    
    # Colors
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    DEFAULT = '\033[39m'
    
    # Backgrounds
    BG_BLACK = '\033[40m'
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_BLUE = '\033[44m'
    BG_MAGENTA = '\033[45m'
    BG_CYAN = '\033[46m'
    BG_WHITE = '\033[47m'
    BG_DEFAULT = '\033[49m'
    
    # Bright colors
    BRIGHT_BLACK = '\033[90m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'

class TerminalControl:
    """Terminal control"""
    
    @staticmethod
    def clear_screen():
        """Clear terminal screen"""
        # Android Termux compatible
        if sys.platform == "linux" or sys.platform == "linux2":
            try:
                os.system('clear')
            except:
                sys.stdout.write('\033[2J\033[H')
                sys.stdout.flush()
        else:
            sys.stdout.write('\033[2J\033[H')
            sys.stdout.flush()
    
    @staticmethod
    def clear_line():
        """Clear current line"""
        sys.stdout.write('\033[2K\r')
        sys.stdout.flush()
    
    @staticmethod
    def move_cursor(x, y):
        """Move cursor to position"""
        sys.stdout.write(f'\033[{y};{x}H')
        sys.stdout.flush()
    
    @staticmethod
    def get_terminal_size():
        """Get terminal size"""
        try:
            import shutil
            size = shutil.get_terminal_size((80, 24))
            return size.lines, size.columns
        except:
            return 24, 80

class InputHandler:
    """Simple input handler"""
    
    @staticmethod
    def get_input(prompt=""):
        """Get input with prompt"""
        try:
            return input(prompt)
        except EOFError:
            return "quit"
        except KeyboardInterrupt:
            raise

# ============================================================
# FIXED CHESS ENGINE
# ============================================================

class ChessEngine:
    """Fixed Stockfish engine wrapper with error handling"""
    
    def __init__(self):
        self.engine = None
        self.engine_path = self._find_engine()
        self.initialized = False
        if CHESS_AVAILABLE:
            self._init_engine()
    def _ensure_engine(self):
        if self.engine and self.initialized:
                return True

        try:
                if self.engine:
                        self.engine.quit()
        except:
                pass

        self.engine = None
        self.initialized = False

        print("Restarting Stockfish...")
        self._init_engine()

        return self.engine is not None and self.initialized

    def _find_engine(self):
        """
        STRICT Termux mode:
        - engine MUST be ./stockfish
        - current working directory
        - no extension
        """

        engine_path = "./stockfish"

        if not os.path.isfile(engine_path):
                print(f"{TerminalColors.RED}Stockfish not found:{TerminalColors.RESET} {engine_path}")
                print(f"{TerminalColors.YELLOW}You must run SHCHESS from the directory containing ./stockfish{TerminalColors.RESET}")
                return None

        # Ensure executable (Android / Linux)
        try:
                os.chmod(engine_path, 0o755)
        except Exception:
                pass

        print(f"{TerminalColors.GREEN}Using Stockfish:{TerminalColors.RESET} {engine_path}")
        return engine_path

    def _init_engine(self):
        self.initialized = False
        self.engine = None

        engine_path = "./stockfish"

        if not os.path.isfile(engine_path):
                print("FATAL: ./stockfish not found")
                return

        try:
                print("Starting Stockfish...")

                engine = chess.engine.SimpleEngine.popen_uci(engine_path)

                # Apply options ONCE, explicitly
                engine.configure({
                    "Threads": 113,
                    "Hash": 7900,
                    "MultiPV": 1,
                    "Use NNUE": True
                })
                engine.ping()  # hard sync
                self.engine = engine
                self.initialized = True

                print("Stockfish ready")
                print("  Engine  : ./stockfish")
                print("  Threads : 7")
                print("  Hash    : 16 MB")
                print("  NNUE    : ON")

        except Exception as e:
                print(f"Stockfish failed: {e}")
                try:
                        engine.quit()
                except:
                        pass
                self.engine = None
                self.initialized = False

    def analyze(self, board, depth=20, time_limit=1.0):
        if not CHESS_AVAILABLE:
                return {'error': 'python-chess not available'}

        # 🔥 THIS WAS MISSING
        if not self._ensure_engine():
                return {'error': 'Engine not available (restart failed)'}

        root_board = board.copy(stack=False)

        try:
                info = self.engine.analyse(
                        root_board,
                        chess.engine.Limit(depth=depth, time=time_limit)
                )
                score = info.get("score")
                evaluation = "N/A"
                if score:
                        pov = score.pov(board.turn)
                        if pov.is_mate():
                                mate = pov.mate()
                                if mate is not None:
                                        evaluation = f"Mate in {abs(mate)}"
                        else:
                                cp = pov.score(mate_score=100000)
                                if cp is not None:
                                        evaluation = f"{cp/100:+.2f}"

                pv = info.get("pv", [])
                pv_san = []
                tmp = board.copy()
                for move in pv[:5]:
                        try:
                                pv_san.append(tmp.san(move))
                                tmp.push(move)
                        except:
                                break
                nodes = info.get("nodes", 0) or 0
                time_ms = info.get("time", 0) or 0

                knps = 0.0
                if nodes and time_ms > 0:
                    knps = (nodes / (time_ms / 1000.0)) / 1000.0


                return {
                        'success': True,
                        'evaluation': evaluation,
                        'pv': ' '.join(pv_san),
                        'depth': depth,
                        'best_move': pv_san[0] if pv_san else None,
                        'knps': knps
                }

        except chess.engine.EngineTerminatedError:
                # Engine crashed → mark dead
                self.initialized = False
                self.engine = None
                return {'error': 'Engine crashed'}

        except Exception as e:
                return {'error': str(e)}
   
    def get_best_move(self, board, time_limit=1.0):
        """Get best move"""
        if not CHESS_AVAILABLE:
            return None
        
        if not self.initialized or not self.engine:
            return None
        
        try:
            result = self.engine.play(board, chess.engine.Limit(time=time_limit))
            return board.san(result.move)
        except:
            return None
    
    def close(self):
        """Close engine"""
        if self.engine:
            try:
                self.engine.quit()
            except Exception:
                pass

# ============================================================
# FILE TYPER
# ============================================================

class FileTyper:
    """Type files with animation"""
    
    def __init__(self, terminal):
        self.terminal = terminal
        self.typing_thread = None
        self.stop_typing = threading.Event()
        self.typing_active = False
    
    def type_file(self, filename, update_interval=2.0, show_comments=True):
        """Type a file with animation"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}\n"
        
        if not os.path.exists(filename):
            return f"{TerminalColors.RED}File not found: {filename}{TerminalColors.RESET}\n"
        
        # Stop any existing typing
        self.stop_typing.set()
        if self.typing_thread and self.typing_thread.is_alive():
            self.typing_thread.join(timeout=1.0)
        self.stop_typing.clear()
        
        ext = os.path.splitext(filename)[1].lower()
        
        if ext == '.pgn':
            return self._type_pgn_file(filename, update_interval, show_comments)
        else:
            try:
                with open(filename, 'r') as f:
                    content = f.read()
                return self._type_text_file(content, update_interval)
            except Exception as e:
                return f"{TerminalColors.RED}Error reading file: {e}{TerminalColors.RESET}\n"
    def _type_pgn_file(self, filename, update_interval, show_comments):
        """MINIMAL working PGN typer"""

        try:
            with open(filename, "r", encoding="utf-8", errors="ignore") as f:
                pgn_text = f.read()

            game = chess.pgn.read_game(io.StringIO(pgn_text))
            if not game:
                return f"{TerminalColors.RED}Invalid PGN{TerminalColors.RESET}\n"

            # Extract moves ONCE
            board = game.board()
            moves = list(game.mainline_moves())
            total_moves = len(moves)

            if total_moves == 0:
                return f"{TerminalColors.YELLOW}No moves in PGN{TerminalColors.RESET}\n"

            # Start thread
            self.typing_active = True
            self.typing_thread = threading.Thread(
                target=self._type_pgn_thread,
                args=(game, board, moves, update_interval, show_comments),
                daemon=True
            )
            self.typing_thread.start()

            return (
                f"{TerminalColors.CYAN}Typing PGN...{TerminalColors.RESET}\n"
                f"Total moves: {total_moves}\n"
                f"Delay: {update_interval}s\n"
                f"Type 'stop' to cancel\n"
            )

        except Exception as e:
            return f"{TerminalColors.RED}PGN error: {e}{TerminalColors.RESET}\n"
                
    def _type_pgn_thread(self, game, board, moves, update_interval, show_comments):
        try:
            self.terminal.load_game(game)

            current_move = 0

            for i, move in enumerate(moves):
                if self.stop_typing.is_set():
                    break

                if move not in board.legal_moves:
                    print(f"{TerminalColors.RED}Illegal move{TerminalColors.RESET}")
                    break

                san = board.san(move)
                board.push(move)
                self.terminal.board.push(move)

                current_move += 1
                self.terminal.move_index = current_move

                # Correct move numbering (NO manual turn tracking)
                if board.turn:
                    move_text = f"{board.fullmove_number - 1}... {san}"
                else:
                    move_text = f"{board.fullmove_number}. {san}"

                print(
                    f"{TerminalColors.GREEN}"
                    f"{move_text} (Move {current_move}/{len(moves)})"
                    f"{TerminalColors.RESET}"
                )

                self.terminal.display_board()

                if board.is_checkmate():
                    print(f"{TerminalColors.RED}CHECKMATE!{TerminalColors.RESET}")
                    break
                if board.is_stalemate():
                    print(f"{TerminalColors.YELLOW}STALEMATE!{TerminalColors.RESET}")
                    break

                time.sleep(update_interval)

            if self.stop_typing.is_set():
                print(f"{TerminalColors.YELLOW}Typing stopped.{TerminalColors.RESET}")
            else:
                print(f"{TerminalColors.GREEN}Finished typing {current_move} moves.{TerminalColors.RESET}")

        except Exception as e:
            print(f"{TerminalColors.RED}Typing error: {e}{TerminalColors.RESET}")

        finally:
            self.typing_active = False
            self.stop_typing.clear()
          

    def _type_text_file(self, content, update_interval):
        """Type text file"""
        lines = content.split('\n')
        
        def type_thread():
            for line in lines:
                if self.stop_typing.is_set():
                    break
                print(line)
                time.sleep(update_interval)
            
            if self.stop_typing.is_set():
                print(f"{TerminalColors.YELLOW}Typing stopped.{TerminalColors.RESET}")
            else:
                print(f"{TerminalColors.GREEN}Finished.{TerminalColors.RESET}")
        
        self.typing_active = True
        self.typing_thread = threading.Thread(target=type_thread, daemon=True)
        self.typing_thread.start()
        
        return f"{TerminalColors.CYAN}Typing text file ({len(lines)} lines)...{TerminalColors.RESET}\n"
    
    def stop_typing_command(self):
        """Stop typing"""
        if not self.typing_active:
            return f"{TerminalColors.YELLOW}No typing in progress.{TerminalColors.RESET}\n"
        
        self.stop_typing.set()
        if self.typing_thread and self.typing_thread.is_alive():
            self.typing_thread.join(timeout=0.5)
        
        self.typing_active = False
        return f"{TerminalColors.YELLOW}Typing stopped.{TerminalColors.RESET}\n"

# ============================================================
# DATABASE EXPLORER
# ============================================================

class DatabaseExplorer:
    """SQLite3 database explorer - TERMINAL VERSION"""
    
    def __init__(self, terminal):
        self.terminal = terminal
        self.db_path = None
        self.conn = None
        self.cursor = None
        self.current_table = None
        self.history = []
        self.running = False
        self.session_id = self.generate_session_id()
        
        # Pagination
        self.page = 0
        self.page_size = 20
        self.total_rows = 0
        self.total_pages = 0
    
    def generate_session_id(self) -> str:
        """Generate unique session ID"""
        timestamp = str(time.time()).encode()
        return hashlib.md5(timestamp).hexdigest()[:8]
    
    def load_endgame_to_board(self, endgame_id: int):
        """
        Load an endgame by ID.
        PRIMARY: load full PGN game from pgn_excerpt
        FALLBACK: load position from FEN (if not PENDING)
        Returns: (success, message, board, game, row_dict)
        """
        if not CHESS_AVAILABLE:
            return False, "python-chess not available", None, None, None
        
        try:
            self.cursor.execute("SELECT * FROM endgames WHERE id = ?", (endgame_id,))
            row = self.cursor.fetchone()
            if not row:
                return False, "Endgame not found", None, None, None

            # Normalize row to dict (works for sqlite3.Row or tuple)
            if isinstance(row, sqlite3.Row):
                data = dict(row)
            else:
                cols = [d[0] for d in self.cursor.description]
                data = dict(zip(cols, row))

            pgn_text = (data.get("pgn_excerpt") or "").strip()
            fen = (data.get("fen") or "").strip()

            # -----------------------------
            # PRIMARY: FULL PGN GAME
            # -----------------------------
            if pgn_text:
                try:
                    game = chess.pgn.read_game(io.StringIO(pgn_text))
                    if not game:
                        return False, "PGN parse failed", None, None, data

                    board = game.board()
                    for mv in game.mainline_moves():
                        board.push(mv)

                    return True, "", board, game, data

                except Exception as e:
                    return False, f"PGN error: {e}", None, None, data

            # -----------------------------
            # FALLBACK: FEN ONLY
            # -----------------------------
            if fen and fen.upper() != "PENDING":
                try:
                    board = chess.Board(fen)
                    game = chess.pgn.Game()
                    game.headers["FEN"] = board.fen()
                    game.headers["SetUp"] = "1"
                    return True, "", board, game, data
                except Exception as e:
                    return False, f"Invalid FEN: {e}", None, None, data

            return False, "No PGN or valid FEN in row", None, None, data

        except Exception as e:
            return False, f"DB error: {e}", None, None, None
    
    def connect(self, db_path: str = None) -> bool:
        """Connect to SQLite database"""
        if db_path:
            self.db_path = db_path
        else:
            # Default path for chess_content.db
            self.db_path = "content/database/chess_content.db"
        
        if not self.db_path:
            return False
        
        if not os.path.exists(self.db_path):
            # Try to find it in common locations
            possible_paths = [
                self.db_path,
                "chess_content.db",
                "../content/database/chess_content.db",
                "../../content/database/chess_content.db",
                os.path.join(os.path.dirname(__file__), "content/database/chess_content.db"),
                os.path.join(os.getcwd(), "chess_content.db"),
                os.path.join(os.getcwd(), "content/database/chess_content.db"),
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    self.db_path = path
                    break
            else:
                return False
        
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            self.cursor = self.conn.cursor()
            print(f"{TerminalColors.GREEN}Connected to database: {self.db_path}{TerminalColors.RESET}")
            return True
        except Exception as e:
            print(f"{TerminalColors.RED}Database error: {e}{TerminalColors.RESET}")
            return False
    
    def disconnect(self):
        """Disconnect from database"""
        if self.conn:
            self.conn.close()
    
    def get_tables(self):
        """Get list of tables"""
        try:
            self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            return [row[0] for row in self.cursor.fetchall()]
        except:
            return []
    
    def get_table_info(self, table_name: str) -> List[Dict]:
        """Get column information for a table"""
        try:
            self.cursor.execute(f"PRAGMA table_info({table_name})")
            columns = []
            for row in self.cursor.fetchall():
                columns.append({
                    'cid': row[0],
                    'name': row[1],
                    'type': row[2],
                    'notnull': row[3],
                    'default': row[4],
                    'pk': row[5]
                })
            return columns
        except Exception as e:
            return []
    
    def get_table_stats(self, table_name: str) -> Dict:
        """Get statistics for a table"""
        stats = {}
        try:
            # Row count
            self.cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            stats['row_count'] = self.cursor.fetchone()[0]
            
            # Column count
            columns = self.get_table_info(table_name)
            stats['column_count'] = len(columns)
            
            return stats
        except Exception as e:
            return {}
    
    def execute_query(self, query: str, limit: int = 100) -> Dict:
        """Execute SQL query with safety limits"""
        result = {'success': False, 'data': [], 'columns': [], 'rowcount': 0, 'error': None}
        
        # Basic safety check
        query_lower = query.lower().strip()
        destructive_keywords = ['drop', 'delete', 'update', 'insert', 'alter', 'truncate']
        
        if any(keyword in query_lower.split() for keyword in destructive_keywords):
            print(f"{TerminalColors.RED}[WARNING] Destructive operation detected.{TerminalColors.RESET}")
        
        try:
            # Add LIMIT if not present and it's a SELECT
            if query_lower.startswith('select') and 'limit' not in query_lower:
                if ';' in query:
                    query = query.replace(';', f' LIMIT {limit};')
                else:
                    query = f'{query} LIMIT {limit}'
            
            self.cursor.execute(query)
            
            if query_lower.startswith('select'):
                data = self.cursor.fetchall()
                result['data'] = data
                result['columns'] = [description[0] for description in self.cursor.description]
                result['rowcount'] = len(data)
            else:
                self.conn.commit()
                result['rowcount'] = self.cursor.rowcount
            
            result['success'] = True
            self.history.append({
                'timestamp': datetime.now().isoformat(),
                'query': query,
                'success': True
            })
            
        except Exception as e:
            error_msg = str(e)
            result['error'] = error_msg
            self.history.append({
                'timestamp': datetime.now().isoformat(),
                'query': query,
                'success': False,
                'error': error_msg
            })
        
        return result
    
    def search_data(self, table_name: str, search_term: str, columns: List[str] = None):
        """Search for data in table"""
        try:
            if not columns:
                table_info = self.get_table_info(table_name)
                columns = [col['name'] for col in table_info]
            
            conditions = []
            params = []
            
            for column in columns:
                conditions.append(f"{column} LIKE ?")
                params.append(f"%{search_term}%")
            
            where_clause = " OR ".join(conditions)
            query = f"SELECT * FROM {table_name} WHERE {where_clause} LIMIT 50"
            
            self.cursor.execute(query, params)
            data = self.cursor.fetchall()
            
            return {
                'success': True,
                'data': data,
                'columns': [description[0] for description in self.cursor.description],
                'rowcount': len(data)
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_endgame_positions(self, limit=20, offset=0):
        """Get endgame positions from database"""
        try:
            self.cursor.execute(
                """
                SELECT id, fen, side_to_move, material, event,
                       white, black, result, move_number, pgn_excerpt, notes
                FROM endgames
                ORDER BY id ASC
                LIMIT ? OFFSET ?
                """,
                (limit, offset)
            )
            rows = self.cursor.fetchall()

            return [
                {
                    "id": r[0],
                    "fen": r[1],
                    "side_to_move": r[2],
                    "material": r[3],
                    "event": r[4],
                    "white": r[5],
                    "black": r[6],
                    "result": r[7],
                    "move_number": r[8],
                    "pgn_excerpt": r[9],
                    "notes": r[10],
                }
                for r in rows
            ]
        except Exception as e:
            print(f"{TerminalColors.RED}Error fetching endgames: {e}{TerminalColors.RESET}")
            return []
    
    def get_endgame_count(self) -> int:
        """Get total count of endgames"""
        try:
            self.cursor.execute("SELECT COUNT(*) FROM endgames")
            return self.cursor.fetchone()[0]
        except:
            return 0
    
    def visualize_schema(self) -> str:
        """Create ASCII visualization of database schema"""
        try:
            self.cursor.execute("""
                SELECT 
                    m.name as table_name,
                    m.sql as table_sql,
                    p.name as column_name,
                    p.type as column_type,
                    p.pk as is_primary
                FROM sqlite_master m
                LEFT JOIN pragma_table_info(m.name) p
                WHERE m.type = 'table'
                ORDER BY m.name, p.cid
            """)
            
            tables = {}
            for row in self.cursor.fetchall():
                table_name = row[0]
                if table_name not in tables:
                    tables[table_name] = {
                        'sql': row[1],
                        'columns': []
                    }
                if row[2]:  # column_name can be None
                    tables[table_name]['columns'].append({
                        'name': row[2],
                        'type': row[3],
                        'pk': row[4]
                    })
            
            output = ["\n" + "═" * 80]
            output.append("DATABASE SCHEMA VISUALIZATION")
            output.append("═" * 80)
            
            for table_name, info in tables.items():
                output.append(f"\n┌─ TABLE: {table_name}")
                output.append("│")
                
                for col in info['columns']:
                    pk_marker = " 🔑" if col['pk'] else ""
                    output.append(f"├─ {col['name']:20} {col['type']:15}{pk_marker}")
                
                output.append("└─")
            
            output.append("\n" + "═" * 80)
            
            return "\n".join(output)
            
        except Exception as e:
            return f"Schema visualization failed: {e}"
    
    def run(self):
        """Run database explorer with command loop"""
        if not self.connect():
            print(f"{TerminalColors.RED}Failed to connect to database{TerminalColors.RESET}")
            return False
        
        self.running = True
        print(f"{TerminalColors.CYAN}{'='*60}{TerminalColors.RESET}")
        print(f"{TerminalColors.BRIGHT_CYAN}DATABASE EXPLORER{TerminalColors.RESET}")
        print(f"{TerminalColors.CYAN}{'='*60}{TerminalColors.RESET}")
        print(f"{TerminalColors.YELLOW}Type 'help' for commands, 'quit' to exit{TerminalColors.RESET}")
        
        # Show tables
        tables = self.get_tables()
        if tables:
            print(f"{TerminalColors.CYAN}Tables:{TerminalColors.RESET}")
            for table in tables:
                print(f"  {TerminalColors.YELLOW}{table}{TerminalColors.RESET}")
        else:
            print(f"{TerminalColors.YELLOW}No tables found{TerminalColors.RESET}")
        
        # Command loop
        while self.running:
            try:
                prompt = f"{TerminalColors.MAGENTA}db>{TerminalColors.RESET} "
                cmd = input(prompt).strip()
                
                if cmd:
                    self.process_command(cmd)
                    
            except KeyboardInterrupt:
                print(f"\n{TerminalColors.YELLOW}Use 'quit' to exit{TerminalColors.RESET}")
                continue
            except EOFError:
                break
            except Exception as e:
                print(f"{TerminalColors.RED}Error: {e}{TerminalColors.RESET}")
        
        return True
    
    def process_command(self, command):
        """Process database command"""
        if not self.running:
            return False
        
        parts = command.strip().split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        args = parts[1] if len(parts) > 1 else ""
        
        if cmd == 'quit' or cmd == 'exit':
            self.running = False
            self.disconnect()
            print(f"{TerminalColors.YELLOW}Exited database explorer{TerminalColors.RESET}")
            return True
        
        elif cmd == 'help':
            help_text = f"""
{TerminalColors.CYAN}Database Commands:{TerminalColors.RESET}
  {TerminalColors.YELLOW}tables{TerminalColors.RESET}            - List all tables
  {TerminalColors.YELLOW}schema{TerminalColors.RESET}            - Visualize database schema
  {TerminalColors.YELLOW}use <table>{TerminalColors.RESET}       - Select table
  {TerminalColors.YELLOW}desc <table>{TerminalColors.RESET}      - Describe table structure
  {TerminalColors.YELLOW}stats <table>{TerminalColors.RESET}     - Show table statistics
  {TerminalColors.YELLOW}select <n>{TerminalColors.RESET}        - Select n rows from current table
  {TerminalColors.YELLOW}query <sql>{TerminalColors.RESET}       - Execute SQL query
  {TerminalColors.YELLOW}search <term>{TerminalColors.RESET}     - Search in current table
  {TerminalColors.YELLOW}history{TerminalColors.RESET}           - Show command history
  {TerminalColors.YELLOW}endgames{TerminalColors.RESET}          - List endgame positions
  {TerminalColors.YELLOW}load <id>{TerminalColors.RESET}         - Load endgame to chess board
  {TerminalColors.YELLOW}next{TerminalColors.RESET}              - Next page (endgames)
  {TerminalColors.YELLOW}prev{TerminalColors.RESET}              - Previous page (endgames)
  {TerminalColors.YELLOW}page <n>{TerminalColors.RESET}          - Go to page n (endgames)
  {TerminalColors.YELLOW}quit{TerminalColors.RESET}              - Exit database explorer
"""
            print(help_text)
            return True
        elif cmd == "puzzle":
            if args == "off":
                self.puzzles.toggle(False)
                print("Puzzle disabled")
            elif args == "on":
                self.puzzles.toggle(True)
                print("Puzzle enabled")
            else:
                ok, msg = self.puzzles.load_puzzle_of_the_day()
                print(msg)
            return True
        
        elif cmd == 'use':
            if not args:
                print(f"{TerminalColors.RED}Usage: use <table_name>{TerminalColors.RESET}")
                return True
            
            tables = self.get_tables()
            if args in tables:
                self.current_table = args
                print(f"{TerminalColors.GREEN}Using table: {args}{TerminalColors.RESET}")
            else:
                print(f"{TerminalColors.RED}Table not found: {args}{TerminalColors.RESET}")
            return True
        
        elif cmd == 'desc':
            table_name = args or self.current_table
            if not table_name:
                print(f"{TerminalColors.RED}No table selected. Use 'use <table>' first.{TerminalColors.RESET}")
                return True
            
            try:
                self.cursor.execute(f"PRAGMA table_info({table_name})")
                columns = self.cursor.fetchall()
                
                if columns:
                    print(f"{TerminalColors.CYAN}Table: {table_name}{TerminalColors.RESET}")
                    for col in columns:
                        constraints = []
                        if col['pk']:
                            constraints.append("PK")
                        if col['notnull']:
                            constraints.append("NOT NULL")
                        
                        constr_str = f" [{', '.join(constraints)}]" if constraints else ""
                        print(f"  {TerminalColors.YELLOW}{col['name']:15}{TerminalColors.RESET} {col['type']:10}{constr_str}")
                else:
                    print(f"{TerminalColors.YELLOW}No columns found{TerminalColors.RESET}")
            except Exception as e:
                print(f"{TerminalColors.RED}Error: {e}{TerminalColors.RESET}")
            return True
        
        elif cmd == 'select':
            if not self.current_table:
                print(f"{TerminalColors.RED}No table selected. Use 'use <table>' first.{TerminalColors.RESET}")
                return True
            
            limit = 10
            if args:
                try:
                    limit = int(args)
                except:
                    pass
            
            try:
                query = f"SELECT * FROM {self.current_table} LIMIT {limit}"
                self.cursor.execute(query)
                rows = self.cursor.fetchall()
                
                if rows:
                    # Get column names
                    column_names = [desc[0] for desc in self.cursor.description]
                    
                    # Print header
                    header = " | ".join([f"{name:15}" for name in column_names])
                    print(f"{TerminalColors.CYAN}{header}{TerminalColors.RESET}")
                    print(f"{TerminalColors.CYAN}{'-'*len(header)}{TerminalColors.RESET}")
                    
                    # Print rows
                    for row in rows:
                        row_str = " | ".join([f"{str(value)[:15]:15}" for value in row])
                        print(row_str)
                    
                    print(f"{TerminalColors.YELLOW}Showing {len(rows)} rows{TerminalColors.RESET}")
                else:
                    print(f"{TerminalColors.YELLOW}No data found{TerminalColors.RESET}")
            except Exception as e:
                print(f"{TerminalColors.RED}Error: {e}{TerminalColors.RESET}")
            return True
        
        elif cmd == 'query':
            if not args:
                print(f"{TerminalColors.RED}Usage: query <sql_statement>{TerminalColors.RESET}")
                return True
            
            try:
                self.cursor.execute(args)
                
                if args.strip().lower().startswith('select'):
                    rows = self.cursor.fetchall()
                    
                    if rows:
                        # Get column names
                        column_names = [desc[0] for desc in self.cursor.description]
                        
                        # Print header
                        header = " | ".join([f"{name:15}" for name in column_names])
                        print(f"{TerminalColors.CYAN}{header}{TerminalColors.RESET}")
                        print(f"{TerminalColors.CYAN}{'-'*len(header)}{TerminalColors.RESET}")
                        
                        # Print rows
                        for row in rows:
                            row_str = " | ".join([f"{str(value)[:15]:15}" for value in row])
                            print(row_str)
                        
                        print(f"{TerminalColors.GREEN}Query OK. Rows: {len(rows)}{TerminalColors.RESET}")
                    else:
                        print(f"{TerminalColors.YELLOW}No data returned{TerminalColors.RESET}")
                else:
                    self.conn.commit()
                    print(f"{TerminalColors.GREEN}Query OK. Rows affected: {self.cursor.rowcount}{TerminalColors.RESET}")
            except Exception as e:
                print(f"{TerminalColors.RED}Error: {e}{TerminalColors.RESET}")
            return True
        
        elif cmd == 'schema':
            schema = self.visualize_schema()
            print(schema)
            return True
        
        elif cmd == 'stats':
            table_name = args or self.current_table
            if not table_name:
                print(f"{TerminalColors.RED}No table specified. Use 'use <table>' first.{TerminalColors.RESET}")
                return True
            
            stats = self.get_table_stats(table_name)
            if stats:
                print(f"\n{TerminalColors.CYAN}Table Statistics: {table_name}{TerminalColors.RESET}")
                print(f"  Rows: {stats.get('row_count', 'N/A')}")
                print(f"  Columns: {stats.get('column_count', 'N/A')}")
            return True
        
        elif cmd == 'search':
            if not self.current_table:
                print(f"{TerminalColors.RED}No table selected. Use 'use <table>' first.{TerminalColors.RESET}")
                return True
            
            if not args:
                print(f"{TerminalColors.RED}Usage: search <term>{TerminalColors.RESET}")
                return True
            
            result = self.search_data(self.current_table, args)
            
            if result['success'] and result['data']:
                print(f"{TerminalColors.GREEN}Found {result['rowcount']} results for '{args}'{TerminalColors.RESET}")
                
                # Print results
                column_names = result['columns']
                header = " | ".join([f"{name:15}" for name in column_names])
                print(f"{TerminalColors.CYAN}{header}{TerminalColors.RESET}")
                print(f"{TerminalColors.CYAN}{'-'*len(header)}{TerminalColors.RESET}")
                
                for row in result['data'][:20]:
                    row_str = " | ".join([f"{str(value)[:15]:15}" for value in row])
                    print(row_str)
                
                if len(result['data']) > 20:
                    print(f"{TerminalColors.YELLOW}Showing 20 of {len(result['data'])} rows{TerminalColors.RESET}")
            elif result['success']:
                print(f"{TerminalColors.YELLOW}No results found for '{args}'{TerminalColors.RESET}")
            else:
                print(f"{TerminalColors.RED}Error: {result.get('error')}{TerminalColors.RESET}")
            return True
        
        elif cmd == 'history':
            if not self.history:
                print(f"{TerminalColors.YELLOW}No command history{TerminalColors.RESET}")
                return True
            
            print(f"\n{TerminalColors.CYAN}Command History:{TerminalColors.RESET}")
            for i, entry in enumerate(self.history[-10:], 1):
                status = "✓" if entry['success'] else "✗"
                time_str = datetime.fromisoformat(entry['timestamp']).strftime("%H:%M:%S")
                query_preview = entry['query'][:50] + "..." if len(entry['query']) > 50 else entry['query']
                print(f"  [{time_str}] {status} {query_preview}")
            return True
        
        elif cmd == 'endgames':
            self.list_endgames()
            return True
        
        elif cmd == 'load':
            if not args:
                print(f"{TerminalColors.RED}Usage: load <endgame_id>{TerminalColors.RESET}")
                return True
            
            try:
                endgame_id = int(args)
            except ValueError:
                print(f"{TerminalColors.RED}Invalid endgame ID{TerminalColors.RESET}")
                return True
            
            self.load_endgame(endgame_id)
            return True
        
        elif cmd == 'next':
            self.next_page()
            return True
        
        elif cmd == 'prev':
            self.prev_page()
            return True
        
        elif cmd == 'page':
            if not args:
                print(f"{TerminalColors.RED}Usage: page <number>{TerminalColors.RESET}")
                return True
            
            try:
                page = int(args) - 1
                self.page = page
                self.list_endgames()
            except ValueError:
                print(f"{TerminalColors.RED}Invalid page number{TerminalColors.RESET}")
            return True
        
        else:
            print(f"{TerminalColors.RED}Unknown command: {cmd}{TerminalColors.RESET}")
            return True
    
    def list_endgames(self):
        """List endgame positions with pagination"""
        # Fetch totals once
        self.total_rows = self.get_endgame_count()
        self.total_pages = max(1, (self.total_rows + self.page_size - 1) // self.page_size)

        # Clamp page
        if self.page < 0:
            self.page = 0
        if self.page >= self.total_pages:
            self.page = self.total_pages - 1

        offset = self.page * self.page_size

        positions = self.get_endgame_positions(
            limit=self.page_size,
            offset=offset
        )

        if not positions:
            print(f"{TerminalColors.YELLOW}No endgame positions{TerminalColors.RESET}")
            return

        print(f"\n{TerminalColors.CYAN}┌─ ENDGAME POSITIONS (page {self.page + 1}/{self.total_pages}, total {self.total_rows}){TerminalColors.RESET}")

        for i, pos in enumerate(positions):
            display = (
                f"├─ [{pos['id']:5d}] "
                f"{pos['white']} vs {pos['black']} "
                f"({pos['result']})"
            )
            print(f"{TerminalColors.YELLOW if i % 2 == 0 else TerminalColors.WHITE}{display}{TerminalColors.RESET}")

        print(f"{TerminalColors.CYAN}└─ Commands: next | prev | page <n> | load <id>{TerminalColors.RESET}")
    
    def next_page(self):
        """Go to next page"""
        self.page += 1
        self.list_endgames()
    
    def prev_page(self):
        """Go to previous page"""
        if self.page > 0:
            self.page -= 1
        self.list_endgames()
    
    def load_endgame(self, endgame_id: int):
        """Load endgame to chess board"""
        # Load from database (PGN-first)
        success, msg, board, game, data = self.load_endgame_to_board(endgame_id)
        if not success or not game:
            print(f"{TerminalColors.RED}Error: {msg}{TerminalColors.RESET}")
            return

        self.terminal.load_game(game)

        # Print database info
        lines = [f"\n{TerminalColors.GREEN}[+] Loaded Endgame ID: {endgame_id}{TerminalColors.RESET}"]

        if data:
            if data.get("event"):
                lines.append(f"Event: {data['event']}")
            if data.get("white"):
                lines.append(f"White: {data['white']}")
            if data.get("black"):
                lines.append(f"Black: {data['black']}")
            if data.get("result"):
                lines.append(f"Result: {data['result']}")
            if data.get("material"):
                lines.append(f"Material: {data['material']}")
            if data.get("notes"):
                lines.append(f"Notes: {data['notes']}")

        for line in lines:
            print(line)

        # Display board
        self.terminal.display_board()

        # Exit DB mode
        self.running = False
        print(f"\n{TerminalColors.YELLOW}[Database explorer closed — returned to chess]{TerminalColors.RESET}")

class MacbethAnalysis:
    """
    Enhanced Macbeth analysis with per-move data storage.
    SELF-CONTAINED: starts its own UCI engine (./stockfish) and shuts it down.
    """

    # LICHESS CONSTANTS
    WIN_CP_K = 0.00368208
    ACC_A = 103.1668
    ACC_B = 0.04354
    ACC_C = 3.1669
    EPS = 1e-9

    def __init__(self, game, depth=24, engine_path="./stockfish"):
        self.game = game
        self.depth = depth
        self.engine_path = engine_path

        # UCI engine handle (python-chess SimpleEngine)
        self.engine = None

        # Core analysis data - STORED PER MOVE
        self.move_data = []
        self.win_before: List[float] = []
        self.win_after: List[float] = []
        self.move_accuracy: List[float] = []
        self.move_macbeth: List[float] = []

        # Game-level results
        self.game_accuracy: Optional[float] = None
        self.macbeth_number: Optional[float] = None
        self.finished = False

        # Threading
        self.analysis_thread = None
        self.stop_event = threading.Event()
        self._engine_lock = threading.Lock()

        # Progress tracking
        self.current_move_index = 0
        self.total_moves = 0
        self.current_accuracy = 0.0
        self.analysis_time = 0.0
        self.last_update_time = 0
        self.update_interval = 0.05

        # TQDM progress
        self._progress_callback = None
        self._last_tqdm_line = ""
        self._last_progress_update = ""

        # Detailed data for GUI
        self.detailed_data = {
            "current_move": 0,
            "total_moves": 0,
            "best_move": None,
            "evaluation": 0.0,
            "depth": 0,
            "analysis_time": 0.0,
            "move_accuracy": 0.0,
            "game_accuracy": 0.0,
            "macbeth_number": 0.0,
            "moves": [],
            "accuracies": [],
            "tqdm": "",
            "knps": 0.0,
        }

    # -------------------------
    # ENGINE LIFECYCLE (NEW)
    # -------------------------
    def _start_engine(self) -> bool:
        if self.engine:
            return True
        if not os.path.isfile(self.engine_path):
            self._send_progress(0, 0, 0.0, 0.0, f"Error: engine not found: {self.engine_path}")
            return False
        try:
            self.engine = chess.engine.SimpleEngine.popen_uci(self.engine_path)
            return True
        except Exception as e:
            self.engine = None
            self._send_progress(0, 0, 0.0, 0.0, f"Error starting engine: {e}")
            return False

    def _stop_engine(self):
        eng = self.engine
        self.engine = None
        if eng:
            try:
                eng.quit()
            except Exception:
                pass

    # -------------------------
    # Your existing helpers below (unchanged)
    # -------------------------
    def _format_tqdm_line(self, current, total, percent, accuracy, knps, san):
        bar_width = 12
        filled = int(bar_width * current / max(total, 1))
        bar = "#" * filled + "." * (bar_width - filled)
        san = san[:8]
        line = (
            f"{current:>3}/{total:<3} "
            f"[{bar}] "
            f"{percent:>5.1f}% "
            f"A{accuracy:>4.1f} "
            f"{knps:>4.0f}k "
            f"{san}"
        )
        return line[:TERM_WIDTH]

    def _cp_to_win(self, cp: float) -> float:
        return 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-self.WIN_CP_K * cp)) - 1.0)

    def _move_accuracy(self, w_before: float, w_after: float) -> float:
        delta = w_before - w_after
        if delta <= 0:
            return 100.0
        acc = self.ACC_A * math.exp(-self.ACC_B * delta) - self.ACC_C
        return max(0.0, min(100.0, acc))

    def _game_accuracy(self) -> float:
        accs = self.move_accuracy
        wins = self.win_after
        if not accs:
            return 100.0

        n = len(accs)
        window = max(4, n // 10)
        weighted = []

        for i in range(0, n):
            lo = max(0, i - window)
            hi = min(n, i + window)

            segment = wins[lo:hi]
            if len(segment) < 2:
                continue

            mean = sum(segment) / len(segment)
            variance = sum((x - mean) ** 2 for x in segment) / len(segment)
            volatility = math.sqrt(variance)
            weighted.append((accs[i], volatility))

        if not weighted:
            vol_mean = sum(accs) / len(accs)
        else:
            num = sum(a * v for a, v in weighted)
            den = sum(v for _, v in weighted) + self.EPS
            vol_mean = num / den

        denom = sum(1.0 / max(a, self.EPS) for a in accs)
        harm = len(accs) / denom
        return (vol_mean + harm) / 2.0

    def _accuracy_to_macbeth(self, acc: float) -> float:
        damage = max(100.0 - acc, self.EPS)
        return math.log10(damage)

    def _extract_knps_live(self, info: dict) -> float:
        try:
            nps = info.get("nps", None)
            if nps is not None:
                return float(nps) / 1000.0
            nodes = info.get("nodes", 0) or 0
            time_ms = info.get("time", 0) or 0
            if nodes and time_ms and time_ms > 0:
                return (nodes / (time_ms / 1000.0)) / 1000.0
        except Exception:
            pass
        return 0.0

    def _stream_analysis(
        self,
        board: chess.Board,
        *,
        current_done: int,
        total_moves: int,
        show_san: str,
        current_accuracy: float,
        phase: str = "",
        gui_emit_interval: float = 0.20,
        engine_poll_interval: float = 0.01,
    ) -> dict:
        last_gui_emit = 0.0
        last_knps = 0.0
        last_info = {}

        percent = (current_done / total_moves) * 100 if total_moves else 0.0
        limit = chess.engine.Limit(depth=self.depth)

        with self._engine_lock:
            with self.engine.analysis(board, limit, info=chess.engine.INFO_ALL) as analysis:
                for info in analysis:
                    if self.stop_event.is_set():
                        break

                    last_info = info or last_info
                    k = self._extract_knps_live(info)
                    if k:
                        last_knps = k

                    now = time.time()
                    if (now - last_gui_emit) >= gui_emit_interval:
                        last_gui_emit = now
                        line = self._format_tqdm_line(
                            current_done, total_moves, percent,
                            current_accuracy, last_knps, show_san
                        )
                        if phase:
                            line = f"{line} [{phase}]"
                        self._send_progress(current_done, total_moves, current_accuracy, last_knps, line)

                    time.sleep(engine_poll_interval)

                try:
                    final_info = analysis.info
                except Exception:
                    final_info = last_info

        return final_info or last_info

    def run_async(self, board: chess.Board, progress_callback=None):
        self.stop_event.clear()
        self.finished = False
        self._progress_callback = progress_callback

        # NEW: engine starts before thread begins
        if not self._start_engine():
            self.finished = True
            return False

        self.analysis_thread = threading.Thread(
            target=self._run_analysis_safe,
            args=(board.copy(), progress_callback),
            daemon=True
        )
        self.analysis_thread.start()
        return True

    def _run_analysis_safe(self, board: chess.Board, progress_callback=None):
        try:
            self._run_analysis(board, progress_callback)
        finally:
            # NEW: always shutdown engine
            self._stop_engine()

    def _run_analysis(self, board: chess.Board, progress_callback=None):
        """
        Main analysis loop - FIXED VERSION
        Stores complete data for each move for navigation
        """
        temp_board = board.copy()
        
        # Clear move history
        while temp_board.move_stack:
            temp_board.pop()

        moves = list(board.move_stack)
        total_moves = len(moves)
        
        # Reset all data structures
        self.move_data = []  # NEW: Store complete move data
        self.win_before = []
        self.win_after = []
        self.move_accuracy = []
        self.move_macbeth = []
        self.total_moves = total_moves
        self.current_move_index = 0
        self.detailed_data["moves"] = []
        self.detailed_data["accuracies"] = []
        
        start_time = time.time()
        self._analysis_start_time = start_time
        self.last_update_time = start_time
        
        try:
            # Send initial progress
            if progress_callback:
                self._send_progress(0, total_moves, 0.0, 0.0, "Starting...")
            
            for idx, move in enumerate(moves):
                if self.stop_event.is_set():
                    self._send_progress(idx, total_moves, 0.0, 0.0, "Stopped by user")
                    break

                self.current_move_index = idx + 1
                
                try:
                    # Get move SAN for display
                    move_san = temp_board.san(move)
                    move_number = (idx // 2) + 1
                    move_color = "White" if temp_board.turn == chess.WHITE else "Black"
                    
                    # Evaluate position BEFORE move
                    info_before = self._stream_analysis(
                        temp_board,
                        current_done=idx,
                        total_moves=total_moves,
                        show_san=move_san,
                        current_accuracy=self.current_accuracy,
                        phase="pre",
                        gui_emit_interval=0.20,
                        engine_poll_interval=0.01
                    )

                    score_before = info_before["score"].pov(temp_board.turn)
                    cp_before = (
                        10000 if score_before.is_mate() and score_before.mate() > 0
                        else -10000 if score_before.is_mate()
                        else score_before.score() or 0
                    )
                    w_before = self._cp_to_win(cp_before)
                    
                    # Make the move
                    temp_board.push(move)
                    
                    # Evaluate position AFTER move
                    info_after = self._stream_analysis(
                        temp_board,
                        current_done=idx,
                        total_moves=total_moves,
                        show_san=move_san,
                        current_accuracy=self.current_accuracy,
                        phase="post",
                        gui_emit_interval=0.20,
                        engine_poll_interval=0.01
                    )

                    # POV from player who just moved
                    score_after = info_after["score"].pov(not temp_board.turn)
                    cp_after = (
                        10000 if score_after.is_mate() and score_after.mate() > 0
                        else -10000 if score_after.is_mate()
                        else score_after.score() or 0
                    )
                    w_after = self._cp_to_win(cp_after)
                    
                    # Calculate accuracy and macbeth
                    acc = self._move_accuracy(w_before, w_after)
                    macbeth = self._accuracy_to_macbeth(acc)
                    
                    # Extract speed
                    knps = self._extract_knps_live(info_after)
                    
                    # NEW: Store complete move data for navigation
                    move_info = {
                        'index': idx,
                        'move_number': move_number,
                        'san': move_san,
                        'uci': move.uci(),
                        'color': move_color,
                        'win_before': w_before,
                        'win_after': w_after,
                        'cp_before': cp_before,
                        'cp_after': cp_after,
                        'accuracy': acc,
                        'macbeth': macbeth,
                        'knps': knps,
                    }
                    self.move_data.append(move_info)
                    
                    # Store in legacy arrays for compatibility
                    self.win_before.append(w_before)
                    self.win_after.append(w_after)
                    self.move_accuracy.append(acc)
                    self.move_macbeth.append(macbeth)
                    self.current_accuracy = acc
                    
                    # Update detailed data
                    self.detailed_data["moves"].append(move_san)
                    self.detailed_data["accuracies"].append(acc)
                    self.detailed_data["knps"] = knps
                    
                    # Calculate progress percentage
                    progress_pct = ((idx + 1) / total_moves) * 100
                    
                    # Update GUI
                    current_time = time.time()
                    if (current_time - self.last_update_time >= self.update_interval or 
                        idx == len(moves) - 1):
                        
                        tqdm_line = self._format_tqdm_line(
                            idx + 1, total_moves, progress_pct, acc, knps, move_san
                        )
                        
                        self._send_progress(idx + 1, total_moves, acc, knps, tqdm_line)
                        self.last_update_time = current_time
                    
                except Exception as e:
                    # On error, fill with defaults
                    move_info = {
                        'index': idx,
                        'move_number': (idx // 2) + 1,
                        'san': str(move),
                        'uci': move.uci(),
                        'color': 'Unknown',
                        'win_before': 50.0,
                        'win_after': 50.0,
                        'cp_before': 0,
                        'cp_after': 0,
                        'accuracy': 50.0,
                        'macbeth': self._accuracy_to_macbeth(50.0),
                        'knps': 0.0,
                        'error': str(e)
                    }
                    self.move_data.append(move_info)
                    
                    self.win_before.append(50.0)
                    self.win_after.append(50.0)
                    self.move_accuracy.append(50.0)
                    self.move_macbeth.append(self._accuracy_to_macbeth(50.0))
                    self.detailed_data["moves"].append(str(move))
                    self.detailed_data["accuracies"].append(50.0)
                    
                    self._send_progress(idx + 1, total_moves, 50.0, 0.0, f"Error: {e}")
            
            # Final calculations if not stopped
            if not self.stop_event.is_set() and len(self.move_accuracy) > 0:
                self.analysis_time = time.time() - start_time
                
                self.game_accuracy = self._game_accuracy()
                base_macbeth = self._accuracy_to_macbeth(self.game_accuracy)
                self.macbeth_number = base_macbeth * 5  # Apply multiplier
                
                # Final summary
                final_line = (
                    f"✓ Analysis complete! "
                    f"Time: {self.analysis_time:.1f}s | "
                    f"Game Accuracy: {self.game_accuracy:.2f}% | "
                    f"Macbeth Number: {self.macbeth_number:.3f}"
                )
                
                self._send_progress(
                    total_moves, total_moves, 
                    self.game_accuracy, 
                    self.detailed_data.get("knps", 0.0),
                    final_line
                )
                
                # Print newline after completion to move to next line
                print()
                
                self.finished = True
            else:
                self._send_progress(0, total_moves, 0.0, 0.0, "No moves analyzed")
                print()
                    
        except Exception as e:
            error_line = f"✗ Analysis failed: {e}"
            self._send_progress(0, total_moves, 0.0, 0.0, error_line)
            print()  # Newline after error
        finally:
            if not self.finished:
                self.finished = True

    def _send_progress(self, current, total, accuracy, knps, message):
        if len(message) > TERM_WIDTH:
                message = message[:TERM_WIDTH - 1]

        # Carriage return overwrite - ALWAYS print to terminal
        out = "\r" + message.ljust(TERM_WIDTH)
        self._last_tqdm_line = out
        
        # Print directly to terminal for tqdm effect
        print(out, end="", flush=True)

        if self._progress_callback:
                self._progress_callback(current, total, accuracy, {
                        "tqdm": out
                })


    def stop(self):
        """Stop ongoing analysis"""
        self.stop_event.set()
        if self.analysis_thread and self.analysis_thread.is_alive():
            self.analysis_thread.join(timeout=1.0)

    # NEW: Enhanced getter methods for navigation
    def get_move_data(self, index: int) -> Optional[Dict[str, Any]]:
        """Get complete data for a specific move"""
        if 0 <= index < len(self.move_data):
            return self.move_data[index].copy()
        return None

    def get_macbeth_number(self) -> Optional[float]:
        return self.macbeth_number

    def get_game_accuracy(self) -> Optional[float]:
        return self.game_accuracy

    def get_move_accuracy(self, index: int) -> Optional[float]:
        if 0 <= index < len(self.move_accuracy):
            return self.move_accuracy[index]
        return None

    def get_move_macbeth(self, index: int) -> Optional[float]:
        """Get Macbeth number for specific move"""
        if 0 <= index < len(self.move_macbeth):
            return self.move_macbeth[index]
        return None

    def get_all_move_accuracies(self) -> List[float]:
        return self.move_accuracy.copy()

    def get_all_move_data(self) -> List[Dict[str, Any]]:
        """Get all move data"""
        return [m.copy() for m in self.move_data]

    def get_move_count(self) -> int:
        return len(self.move_accuracy)

    def get_detailed_data(self) -> Dict[str, Any]:
        return self.detailed_data.copy()

    def get_progress_summary(self) -> str:
        """Get human-readable progress summary"""
        if not self.move_accuracy:
            return "Analysis not started yet."
        
        completed = len(self.move_accuracy)
        total = self.total_moves
        progress_pct = (completed / total) * 100 if total > 0 else 0
        
        if completed == 0:
            return f"Starting analysis of {total} moves at depth {self.depth}..."
        
        avg_accuracy = sum(self.move_accuracy) / completed if completed > 0 else 0
        
        lines = [
            f"Progress: {completed}/{total} moves ({progress_pct:.1f}%)",
            f"Current move: {self.current_move_index}/{total}",
            f"Current accuracy: {self.current_accuracy:.1f}%",
            f"Average accuracy: {avg_accuracy:.1f}%",
            f"Speed: {self.detailed_data.get('knps', 0.0):.1f} KN/s",
        ]
        
        if self.finished and self.game_accuracy is not None:
            lines.extend([
                f"Final game accuracy: {self.game_accuracy:.2f}%",
                f"Macbeth Number: {self.macbeth_number:.3f}",
                f"Total time: {self.analysis_time:.1f}s"
            ])
        
        return "\n".join(lines)

# ============================================================
# MOVE NAVIGATOR
# ============================================================


# ============================================================
# FIXED: MoveNavigator with Proper Macbeth Display
# ============================================================

class MoveNavigator:
    """
    Navigate moves using the ChessTerminal game state.
    """

    def __init__(self, chess_tab):
        self.tab = chess_tab

        # Ensure required navigation state exists on the tab
        if not hasattr(self.tab, "game"):
            self.tab.game = None
        if not hasattr(self.tab, "game_node"):
            self.tab.game_node = None
        if not hasattr(self.tab, "mainline_moves"):
            self.tab.mainline_moves = []
        if not hasattr(self.tab, "move_index"):
            self.tab.move_index = 0
        if not hasattr(self.tab, "board"):
            self.tab.board = self.board ()

        # Macbeth integration (optional)
        self.macbeth_analysis = None
        self.current_move_index = 0
        self._macbeth_in_progress = False
        self._macbeth_progress_lines = []

    def _append_text(self, text):
        print(text, end="", flush=True)

    def set_macbeth_analysis(self, analysis: MacbethAnalysis):
        """Set the Macbeth analysis object for navigation"""
        self.macbeth_analysis = analysis
    def cmd_setfen(self, args):
        """
        Load a FEN position directly from command line
        Usage: setfen <fen_string>
        Example: setfen rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1
        """
        if not CHESS_AVAILABLE:
                return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        
        if not args:
                return f"{TerminalColors.RED}Usage: setfen <fen_string>{TerminalColors.RESET}"
        
        # Join all args to form the complete FEN string
        fen_string = " ".join(args)
        
        try:
                # Validate FEN by creating a test board
                test_board = chess.Board(fen_string)
                
                # CRITICAL: Set both board and start_board from FEN
                self.board = chess.Board(fen_string)
                self.start_board = chess.Board(fen_string)
                
                # CRITICAL: Properly initialize game from FEN
                self.game = chess.pgn.Game()
                
                # Set the starting FEN in game headers if not standard starting position
                if fen_string != chess.STARTING_FEN:
                        self.game.headers["FEN"] = fen_string
                        self.game.headers["SetUp"] = "1"
                
                # Initialize game_node at root
                self.game_node = self.game
                
                # Reset move tracking
                self.mainline_moves = []
                self.move_index = 0
                self.start_fen = fen_string
                
                self.display_board()
                return f"{TerminalColors.GREEN}Position loaded from FEN{TerminalColors.RESET}"
                
        except Exception as e:
                return f"{TerminalColors.RED}Invalid FEN: {e}{TerminalColors.RESET}"
                
    def _goto(self, index: int):
        """
        FIXED: Navigate to move with game_node synchronization for showhistory
        Now properly updates game_node so showhistory shows <= CURRENT correctly
        """
        if not hasattr(self.tab, "start_board") or not hasattr(self.tab, "mainline_moves"):
                print("[ERROR] No game loaded")
                return

        # Clamp index
        index = max(0, min(index, len(self.tab.mainline_moves)))

        # Build board state from start position
        board = self.tab.start_board.copy()
        for m in self.tab.mainline_moves[:index]:
                board.push(m)

        self.tab.board = board
        self.tab.move_index = index
        self.current_move_index = index
        
        # CRITICAL FIX: Update game_node to match the current position
        # This is what showhistory uses to mark <= CURRENT
        self.tab.game_node = self.tab.game  # Start at root
        
        # Walk through the game tree to the current position
        for i, move in enumerate(self.tab.mainline_moves[:index]):
                # Find the variation that matches this move
                next_node = None
                for var in self.tab.game_node.variations:
                        if var.move == move:
                                next_node = var
                                break
                
                if next_node:
                        self.tab.game_node = next_node
                else:
                        # If somehow the variation doesn't exist, create it
                        self.tab.game_node = self.tab.game_node.add_variation(move)
        
        # Render board (it prints directly, doesn't return)
        self.tab._render_board()
        
        # FIXED: Add Macbeth analysis if available and complete
        if self.macbeth_analysis:
                if self.macbeth_analysis.finished:
                        output_lines = []
                        
                        if index > 0:
                                # Get move data (moves are 0-indexed in analysis)
                                move_idx = index - 1
                                move_data = self.macbeth_analysis.get_move_data(move_idx)
                                
                                if move_data:
                                        # Display comprehensive move analysis
                                        output_lines.append("")
                                        output_lines.append(
                                                f"M{move_data['move_number']:>2} {move_data['san']:<8} "
                                                f"A:{move_data['accuracy']:>5.1f}% "
                                                f"M:{move_data['macbeth']:.2f}"
                                        )
                                        output_lines.append(
                                                f"CP:{move_data['cp_before']:+4d}->{move_data['cp_after']:+4d} "
                                                f"W:{move_data['win_before']:>4.1f}->{move_data['win_after']:>4.1f}"
                                        )
                                        
                                        # Add comparison to game average
                                        game_acc = self.macbeth_analysis.get_game_accuracy()
                                        if game_acc:
                                                diff = move_data['accuracy'] - game_acc
                                                output_lines.append(f"  vs Avg:   {diff:+.2f}% (Game: {game_acc:.2f}%)")
                                        
                                        output_lines.append("─" * 60)
                        else:
                                # At starting position - show game summary
                                game_acc = self.macbeth_analysis.get_game_accuracy()
                                macbeth = self.macbeth_analysis.get_macbeth_number()
                                
                                if game_acc and macbeth:
                                        output_lines.append("═" * 60)
                                        output_lines.append("                  GAME ANALYSIS SUMMARY")
                                        output_lines.append("═" * 60)
                                        output_lines.append(f"  Game Accuracy:  {game_acc:.2f}%")
                                        output_lines.append(f"  Macbeth Number: {macbeth:.3f}")
                                        output_lines.append(f"  Total Moves:        {len(self.tab.mainline_moves)}")
                                        output_lines.append("═" * 60)
                        
                        # Print all output lines
                        if output_lines:
                                print("\n".join(output_lines))


    def first(self):
        """Go to start of game"""
        self._goto(0)

    def last(self):
        """Go to end of game"""
        self._goto(len(self.tab.mainline_moves))

    def next(self):
        """Go to next move"""
        self._goto(self.tab.move_index + 1)

    def prev(self):
        """Go to previous move"""
        self._goto(self.tab.move_index - 1)

    def goto_move(self, move_number: int):
        """Go to specific move number"""
        self._goto(move_number)

    # Macbeth integration methods
    def start_macbeth_analysis(self, engine, depth=24):
        """
        FIXED: Start Macbeth analysis with proper storage
        """
        if not hasattr(self.tab, "mainline_moves") or not self.tab.mainline_moves:
            return "No game loaded. Load a game first.\n"
        
        if self._macbeth_in_progress:
            return "Macbeth analysis already in progress.\n"
        
        self.tab._append_text("Starting Macbeth analysis...\n", "info")
        self.tab._append_text(f"Analyzing {len(self.tab.mainline_moves)} moves at depth {depth}.\n", "info")
        self.tab._append_text("You can navigate while analysis runs.\n", "info")
        
        # Create NEW analysis object
        self.macbeth_analysis = MacbethAnalysis()
        
        self._macbeth_in_progress = True
        self._macbeth_progress_lines = []
        
        # Progress callback
        def progress_callback(current, total, accuracy, detailed_data=None):
            """Display progress updates"""
            if detailed_data and "tqdm" in detailed_data:
                line = detailed_data["tqdm"]
                
                if line:
                    # Deduplicate
                    if not self._macbeth_progress_lines or line != self._macbeth_progress_lines[-1]:
                        self._macbeth_progress_lines.append(line)
                        self._macbeth_progress_lines = self._macbeth_progress_lines[-5:]
                        
                        display = "\n".join(self._macbeth_progress_lines[-3:])
                        self._append_text("\n" + display + "\n")
            
            # Periodic summary
            if current == total or (current > 0 and current % 10 == 0):
                summary = self.macbeth_analysis.get_progress_summary()
                self._append_text("\n" + summary + "\n")
        
        # Completion monitor
        def monitor_completion():
            if self.macbeth_analysis.analysis_thread:
                self.macbeth_analysis.analysis_thread.join()
            
            self._macbeth_in_progress = False
            
            if self.macbeth_analysis.finished:
                macbeth = self.macbeth_analysis.get_macbeth_number()
                game_acc = self.macbeth_analysis.get_game_accuracy()
                
                self._append_text("\n" + "="*60 + "\n")
                self._append_text("MACBETH ANALYSIS COMPLETE!\n")
                self._append_text("="*60 + "\n")
                
                if game_acc:
                    self._append_text(f"Game Accuracy:  {game_acc:.2f}%\n")
                if macbeth:
                    self._append_text(f"Macbeth Number: {macbeth:.3f}\n")
                
                self._append_text("="*60 + "\n")
                self._append_text("Navigate through moves to see individual accuracies!\n")
                self._append_text("Use '$macbeth summary' for detailed analysis\n")
        
        # Start analysis
        self.macbeth_analysis.run_async(self.tab.board, progress_callback)
        
        # Start monitoring thread
        monitor_thread = threading.Thread(target=monitor_completion, daemon=True)
        monitor_thread.start()
        
        return "Macbeth analysis started. Progress will appear below.\n"

    def stop_macbeth_analysis(self):
        """Stop ongoing Macbeth analysis"""
        if self.macbeth_analysis and self._macbeth_in_progress:
            self.macbeth_analysis.stop()
            self._macbeth_in_progress = False
            return "Macbeth analysis stopped.\n"
        return "No Macbeth analysis in progress.\n"

    def get_macbeth_summary(self):
        """
        FIXED: Get comprehensive Macbeth summary with all stats
        """
        if not self.macbeth_analysis:
            return "No Macbeth analysis available. Run $macbeth first.\n"
        
        if not self.macbeth_analysis.finished:
            if self._macbeth_in_progress:
                return "Macbeth analysis in progress...\n" + self.macbeth_analysis.get_progress_summary() + "\n"
            return "Macbeth analysis incomplete or stopped.\n"
        
        summary = []
        macbeth_num = self.macbeth_analysis.get_macbeth_number()
        game_acc = self.macbeth_analysis.get_game_accuracy()
        
        summary.append("="*60)
        summary.append("MACBETH ANALYSIS RESULTS")
        summary.append("="*60)
        
        if game_acc is not None:
            summary.append(f"Game Accuracy:  {game_acc:.2f}%")
        
        if macbeth_num is not None:
            summary.append(f"Macbeth Number: {macbeth_num:.3f}")
        
        move_accuracies = self.macbeth_analysis.get_all_move_accuracies()
        if move_accuracies:
            summary.append(f"\nMove Statistics:")
            summary.append(f"  Total moves analyzed: {len(move_accuracies)}")
            
            avg_acc = sum(move_accuracies) / len(move_accuracies)
            min_acc = min(move_accuracies)
            max_acc = max(move_accuracies)
            
            best_idx = move_accuracies.index(max_acc)
            worst_idx = move_accuracies.index(min_acc)
            
            summary.append(f"  Average accuracy: {avg_acc:.2f}%")
            summary.append(f"  Best move  (#{best_idx + 1}): {max_acc:.2f}%")
            summary.append(f"  Worst move (#{worst_idx + 1}): {min_acc:.2f}%")
            
            # Distribution
            excellent = sum(1 for acc in move_accuracies if acc >= 90)
            good = sum(1 for acc in move_accuracies if 70 <= acc < 90)
            okay = sum(1 for acc in move_accuracies if 50 <= acc < 70)
            poor = sum(1 for acc in move_accuracies if acc < 50)
            
            summary.append(f"\nAccuracy Distribution:")
            summary.append(f"  Excellent (≥90%):  {excellent:3} moves")
            summary.append(f"  Good (70-89%):     {good:3} moves")
            summary.append(f"  Okay (50-69%):     {okay:3} moves")
            summary.append(f"  Poor (<50%):       {poor:3} moves")
        
        summary.append("="*60)
        summary.append("Navigate with $first/$prev/$next/$last to review moves")
        summary.append("="*60)
        
        return "\n".join(summary) + "\n"

    def get_move_analysis(self, move_num: int):
        """Get detailed analysis for specific move"""
        if not self.macbeth_analysis:
            return "No Macbeth analysis available.\n"
        
        if not self.macbeth_analysis.finished:
            return "Macbeth analysis not complete yet.\n"
        
        if move_num < 1 or move_num > len(self.tab.mainline_moves):
            return f"Invalid move number. Game has {len(self.tab.mainline_moves)} moves.\n"
        
        # Get move data (0-indexed)
        move_idx = move_num - 1
        move_data = self.macbeth_analysis.get_move_data(move_idx)
        
        if not move_data:
            return "No analysis data for this move.\n"
        
        output = []
        output.append("="*60)
        output.append(f"Move #{move_data['move_number']}: {move_data['san']} ({move_data['color']})")
        output.append("="*60)
        output.append(f"Accuracy:     {move_data['accuracy']:.2f}%")
        output.append(f"Macbeth:      {move_data['macbeth']:.3f}")
        output.append(f"Evaluation:   {move_data['cp_before']:+.0f} → {move_data['cp_after']:+.0f} cp")
        output.append(f"Win%:         {move_data['win_before']:.1f}% → {move_data['win_after']:.1f}%")
        
        # Compare to game average
        game_acc = self.macbeth_analysis.get_game_accuracy()
        if game_acc:
            diff = move_data['accuracy'] - game_acc
            output.append(f"vs Game Avg:  {diff:+.2f}% (Avg: {game_acc:.2f}%)")
        
        output.append("="*60)
        
        return "\n".join(output) + "\n"

# ============================================================
# MAIN TERMINAL CLASS
# ============================================================

class ChessTerminal:
    """Main chess terminal application"""
    
    def __init__(self):
        # -----------------------------
        # Identity / prompt
        # -----------------------------
        self.username = self._get_username()

        # -----------------------------
        # Core chess state
        # -----------------------------
        self.board = chess.Board()

        # Game state (None = no game loaded)
        self.game = chess.pgn.Game()
        self.game_node = self.game

        self.mainline_moves = []
        self.move_index = 0
        self.start_board = self.board.copy()

        # -----------------------------
        # Engine / analysis - FIXED: Initialize engine
        # -----------------------------
        self.engine = StockfishEngine("./stockfish")
        self.macbeth = None

        # -----------------------------
        # Display / UI state
        # -----------------------------
        self.flipped = False
        self.show_coords = True

        
        self.file_typer = FileTyper(self)
        self.move_navigator = MoveNavigator(self)
        self.database_explorer = DatabaseExplorer(self)
        
        # State
        self.running = True
        self.in_database_mode = False
        self.move_history = []
        
        # Command history
        self.history = []
        self.history_index = 0
        self.config_manager = ConfigManager("config.json")
        self.puzzle_manager = PuzzleManager(self.config_manager)
        self.endgame_manager = EndgameManager(self.config_manager.get_database_path())

        # -----------------------------
        # New analysis engine (tqdm-like)
        # -----------------------------
        self.analyser = Analyse("./stockfish")
        
        # Initialize
        self._setup_commands()
        
        self.setup_readline()
        self.setup_signal_handlers()
    def _render_board(self):
        return self.display_board()
        
    def _rebuild_game_from_board(self):
        """
        Rebuild game tree from current board position.
        FIXED: Now properly synchronizes game_node with board state
        """
        # Create a fresh game from starting position
        self.game = chess.pgn.Game()
        self.game_node = self.game
        
        # If board is not at starting position, we need to replay moves
        # Get the move stack from the board
        move_stack = list(self.board.move_stack)
        
        # Create a temporary board to replay moves
        temp_board = chess.Board()
        
        # Add each move to the game tree
        for move in move_stack:
                # Add variation to game tree
                self.game_node = self.game_node.add_variation(move)
                temp_board.push(move)
        
        # Update mainline_moves
        self.mainline_moves = list(self.game.mainline_moves())
        self.move_index = len(move_stack)

    def cmd_puzzle(self, args):
        """Load next puzzle (n+1) from database"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"

        puzzle = self.puzzle_manager.load_next_puzzle()
        if puzzle:
            self.board = chess.Board(puzzle["fen"])

            # reset PGN tree to this position (so navigation doesn't explode)
            self.game = chess.pgn.Game()
            self.game.headers["FEN"] = self.board.fen()
            self.game.headers["SetUp"] = "1"
            self.game_node = self.game
            self.mainline_moves = []
            self.move_index = 0

            self.display_board()
            return (
                f"{TerminalColors.CYAN}Puzzle #{puzzle['index']} (DB id {puzzle['id']}){TerminalColors.RESET}\n"
                f"Rating: {puzzle.get('rating', 'N/A')}\n"
                f"Themes: {puzzle.get('themes', 'N/A')}\n"
                f"{TerminalColors.YELLOW}Find the best move!{TerminalColors.RESET}"
            )
        return f"{TerminalColors.RED}No puzzle available (or puzzle mode stopped){TerminalColors.RESET}"

    def cmd_puzzle_stop(self, args):
        """Stop puzzle mode: no more puzzles will load"""
        self.config_manager.set_puzzle_enabled(False)
        return f"{TerminalColors.GREEN}Puzzle mode stopped{TerminalColors.RESET}"

    def cmd_puzzle_start(self, args):
        """Start puzzle mode again"""
        self.config_manager.set_puzzle_enabled(True)

    def cmd_analyse(self, args):
        """Analyse position with tqdm-like progress + KN/s using ./stockfish"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"

        depth = 20
        if args:
            try:
                depth = int(args[0])
            except Exception:
                pass

        # only ./stockfish
        self.analyser.engine_path = "./stockfish"

        self.analyser.analyse_position(self.board, depth=depth)
        return ""
   
    def load_game(self, game):
        """
        Canonical game loader.
        Authoritative and single-source-of-truth.
        FIXED: Now properly advances game_node through all loaded moves
        """

        # --- Core game object ---
        self.game = game
        self.game_node = game  # Start at root

        # --- Moves / navigation ---
        try:
                self.mainline_moves = list(game.mainline_moves())
        except Exception:
                self.mainline_moves = []

        # --- Get starting FEN from game headers or use standard ---
        starting_fen = game.headers.get("FEN", chess.STARTING_FEN)
        self.start_fen = starting_fen
        
        # --- Board initialization ---
        # CRITICAL: Start from the game's actual starting position
        if starting_fen != chess.STARTING_FEN:
                self.board = chess.Board(starting_fen)
                self.start_board = chess.Board(starting_fen)
        else:
                self.board = chess.Board()
                self.start_board = chess.Board()
        
        # CRITICAL FIX: Advance game_node through all loaded moves
        # This ensures game_node stays synchronized with the board
        for mv in self.mainline_moves:
                self.board.push(mv)
                # Find the variation that matches this move and advance to it
                next_node = None
                for var in self.game_node.variations:
                        if var.move == mv:
                                next_node = var
                                break
                if next_node:
                        self.game_node = next_node
                else:
                        # This shouldn't happen with a valid loaded game, but handle it
                        self.game_node = self.game_node.add_variation(mv)

        # --- Position data ---
        self.move_index = len(self.mainline_moves)

        # --- Compatibility flags ---
        if hasattr(self, "loaded_game"):
                self.loaded_game = game
        if hasattr(self, "current_game"):
                self.current_game = game
        if hasattr(self, "active_game"):
                self.active_game = game
        if hasattr(self, "game_loaded"):
                self.game_loaded = True
        if hasattr(self, "has_game_loaded"):
                self.has_game_loaded = True

        # --- Metadata reset ---
        if hasattr(self, "loaded_game_path"):
                self.loaded_game_path = None
                


    def _append_text(self, *args, **kwargs):
        """
        Terminal-safe output sink.
        Accepts any legacy GUI-style calls.
        """
        if not args:
                return

        text = "".join(str(a) for a in args)
        end = kwargs.get("end", "")
        flush = kwargs.get("flush", True)

        print(text, end=end, flush=flush)
    def setup_signal_handlers(self):
        """Setup CTRL+C handler for graceful shutdown"""
        def signal_handler(sig, frame):
                print(f"\n\n{TerminalColors.BRIGHT_YELLOW}{'='*60}{TerminalColors.RESET}")
                print(f"{TerminalColors.BRIGHT_YELLOW}CTRL+C detected - Emergency shutdown{TerminalColors.RESET}")
                print(f"{TerminalColors.BRIGHT_YELLOW}{'='*60}{TerminalColors.RESET}\n")
                
                cleanup_tasks = []
                
                # Stop engine
                if hasattr(self, 'engine') and self.engine:
                        if hasattr(self.engine, 'initialized') and self.engine.initialized:
                                cleanup_tasks.append("Stopping chess engine")
                                try:
                                        self.engine.close()
                                        print(f"{TerminalColors.GREEN}✓ Engine stopped{TerminalColors.RESET}")
                                except Exception:
                                        print(f"{TerminalColors.YELLOW}⚠ Engine stop failed{TerminalColors.RESET}")
                
                # Stop file typer
                if hasattr(self, 'file_typer') and self.file_typer:
                        if hasattr(self.file_typer, 'typing_active') and self.file_typer.typing_active:
                                if hasattr(self.file_typer, 'stop_typing'):
                                        cleanup_tasks.append("Stopping file typer")
                                        try:
                                                self.file_typer.stop_typing_command()
                                                print(f"{TerminalColors.GREEN}✓ File typer stopped{TerminalColors.RESET}")
                                        except Exception:
                                                print(f"{TerminalColors.YELLOW}⚠ File typer stop failed{TerminalColors.RESET}")
                
                # Stop Macbeth analysis
                if hasattr(self, 'move_navigator') and self.move_navigator:
                        if hasattr(self.move_navigator, 'macbeth_analysis') and self.move_navigator.macbeth_analysis:
                                if hasattr(self.move_navigator, '_macbeth_in_progress') and self.move_navigator._macbeth_in_progress:
                                        cleanup_tasks.append("Stopping Macbeth analysis")
                                        try:
                                                self.move_navigator.stop_macbeth_analysis()
                                                print(f"{TerminalColors.GREEN}✓ Macbeth analysis stopped{TerminalColors.RESET}")
                                        except Exception:
                                                print(f"{TerminalColors.YELLOW}⚠ Macbeth stop failed{TerminalColors.RESET}")
                
                # Exit database mode
                if hasattr(self, 'in_database_mode') and self.in_database_mode:
                        cleanup_tasks.append("Closing database")
                        try:
                                self.database_explorer.disconnect()
                                print(f"{TerminalColors.GREEN}✓ Database closed{TerminalColors.RESET}")
                        except:
                                pass
                
                print(f"\n{TerminalColors.CYAN}Goodbye!{TerminalColors.RESET}\n")
                sys.exit(0)
        
        # Register handler
        signal.signal(signal.SIGINT, signal_handler)
        
        # Also handle SIGTERM on Unix
        if hasattr(signal, 'SIGTERM'):
                signal.signal(signal.SIGTERM, signal_handler)


    def setup_readline(self):
        """Setup readline for command history navigation"""
        try:
            import readline
            
            # History file paths - try /content/bash.rc first (Android/Termux), fallback to home
            histfile_paths = [
                "/content/bash.rc",
                os.path.join(os.path.expanduser("~"), ".shchess_history")
            ]
            
            histfile = None
            for path in histfile_paths:
                try:
                    # Try to write to verify we have permission
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    open(path, 'a').close()
                    histfile = path
                    break
                except (OSError, PermissionError):
                    continue
            
            if histfile is None:
                histfile = histfile_paths[-1]  # Use home as last resort
            
            # Load existing history
            try:
                readline.read_history_file(histfile)
            except FileNotFoundError:
                pass
            except Exception:
                # Create empty history file
                try:
                    open(histfile, 'a').close()
                except:
                    pass
            
            # Save history on exit
            import atexit
            def save_hist():
                try:
                    readline.write_history_file(histfile)
                except:
                    pass
            atexit.register(save_hist)
            
            # Set history length
            readline.set_history_length(1000)
            
            # Enable tab completion
            readline.parse_and_bind("tab: complete")
            
            print(f"{TerminalColors.GREEN}Command history enabled (↑/↓) at {histfile}{TerminalColors.RESET}")
            
        except ImportError:
            # readline not available (Windows without pyreadline)
            print(f"{TerminalColors.YELLOW}Note: Install pyreadline3 for command history{TerminalColors.RESET}")
        
    def _advance_game_node(self, move: "chess.Move"):
        """
        Claude.py logic:
        - If move exists as a variation, reuse it
        - else add as variation
        """
        next_node = None
        for var in self.game_node.variations:
                if var.move == move:
                        next_node = var
                        break
        if next_node is None:
                next_node = self.game_node.add_variation(move)
        self.game_node = next_node


    def _rebuild_board_from_node(self):
        """
        Claude.py logic:
        Board is ALWAYS derived from game_node.
        Also rebuild move_index from current node depth.
        """
        # Rebuild board from current node (authoritative)
        self.board = self.game_node.board().copy()

        # Rebuild move_index as path length root -> game_node
        depth = 0
        n = self.game_node
        while n and n.parent:
                depth += 1
                n = n.parent
        self.move_index = depth

    
    def _get_username(self):
        """Get username for custom prompt"""
        try:
            # Try to get from environment
            username = os.environ.get('USER') or os.environ.get('USERNAME') or os.environ.get('LOGNAME')
            if username:
                return username
            
            # Try whoami command
            try:
                result = subprocess.run(['whoami'], capture_output=True, text=True, timeout=1)
                if result.returncode == 0:
                    return result.stdout.strip()
            except Exception:
                pass
            
            # Default
            return "user"
        except:
            return "user"
    
    def _setup_commands(self):
        """Setup command handlers"""
        self.commands = {
            # Basic commands
            'help': self.cmd_help,
            'clear': self.cmd_clear,
            'quit': self.cmd_quit,
            'exit': self.cmd_quit,
            
            # Board commands
            'board': self.cmd_board,
            'flip': self.cmd_flip,
            'fen': self.cmd_fen,
            'legal': self.cmd_legal,
            'reset': self.cmd_reset,
            'undo': self.cmd_undo,
            
            # Navigation
            'first': self.cmd_first,
            'last': self.cmd_last,
            'next': self.cmd_next,
            'prev': self.cmd_prev,
            'previous': self.cmd_prev,
            
            # Game management
            'save': self.cmd_save,
            'load': self.cmd_load,
            'import': self.cmd_import,
            'type': self.cmd_type,
            'stop': self.cmd_stop,
            
            # Puzzles / Endgames
            'puzzle': self.cmd_puzzle,
            'puzzle_start': self.cmd_puzzle_start,
            'puzzle_stop': self.cmd_puzzle_stop,
            # Analyse tqdm-like (replace old analyze behavior)
            'analyze': self.cmd_analyse,
            'eval': self.cmd_eval,
            'best': self.cmd_best,
            'tablebase': self.cmd_tablebase,
            'tb': self.cmd_tablebase,
            
            # Database
            'db': self.cmd_db,
            'database': self.cmd_database,
            
            # Utility
            'history': self.cmd_history,
            'showhistory': self.cmd_showhistory,
            '$boardedit': self.cmd_boardedit,
            'deleteline': self.cmd_deleteline,
            'addcomment': self.cmd_addcomment,
            'makemainline': self.cmd_makemainline,
            
            "macbeth": self.cmd_macbeth,
    # optional aliases (people will type these)
            "$macbeth": self.cmd_macbeth,
        }
    
    def show_welcome(self):
        """Display welcome screen"""
        TerminalControl.clear_screen()
        print(f"{TerminalColors.CYAN}{'='*60}{TerminalColors.RESET}")
        print(f"{TerminalColors.BRIGHT_CYAN}SHCHESS+ TERMINAL{TerminalColors.RESET}")
        print(f"{TerminalColors.CYAN}{'='*60}{TerminalColors.RESET}")
        print(f"{TerminalColors.YELLOW}Type 'help' for commands{TerminalColors.RESET}")
        print(f"{TerminalColors.GREEN}Ready to play!{TerminalColors.RESET}")
        print()

    def display_board(self):
        """Display chess board"""
        if not CHESS_AVAILABLE:
                print(f"{TerminalColors.YELLOW}Chess board unavailable - install python-chess{TerminalColors.RESET}")
                return

        board_str = str(self.board)
        lines = board_str.split('\n')

        # Flip board for black perspective (rotate 180°)
        if self.flipped:
                lines = lines[::-1]  # flip ranks
                processed_lines = []
                for line in lines:
                        line = line[::-1]  # flip files
                        colored_line = ''
                        for char in line:
                                if char.isupper():
                                        colored_line += TerminalColors.BRIGHT_WHITE + char + TerminalColors.RESET
                                elif char.islower():
                                        colored_line += TerminalColors.WHITE + char + TerminalColors.RESET
                                else:
                                        colored_line += char
                        processed_lines.append(colored_line)
                lines = processed_lines
        else:
                processed_lines = []
                for line in lines:
                        colored_line = ''
                        for char in line:
                                if char.isupper():
                                        colored_line += TerminalColors.BRIGHT_WHITE + char + TerminalColors.RESET
                                elif char.islower():
                                        colored_line += TerminalColors.WHITE + char + TerminalColors.RESET
                                else:
                                        colored_line += char
                        processed_lines.append(colored_line)
                lines = processed_lines

        # Add coordinates
        if self.show_coords:
                if not self.flipped:
                        for i in range(8):
                                rank = 8 - i
                                lines[i] = f"{TerminalColors.YELLOW}{rank}{TerminalColors.RESET} {lines[i]}"
                        lines.append(f"  {TerminalColors.YELLOW}a b c d e f g h{TerminalColors.RESET}")
                else:
                        for i in range(8):
                                rank = i + 1
                                lines[i] = f"{TerminalColors.YELLOW}{rank}{TerminalColors.RESET} {lines[i]}"
                        lines.append(f"  {TerminalColors.YELLOW}h g f e d c b a{TerminalColors.RESET}")

        board_str = '\n'.join(lines)

        # Add status
        status = []
        if self.board.is_checkmate():
                status.append(f"{TerminalColors.RED}CHECKMATE{TerminalColors.RESET}")
        elif self.board.is_stalemate():
                status.append(f"{TerminalColors.YELLOW}STALEMATE{TerminalColors.RESET}")
        elif self.board.is_check():
                status.append(f"{TerminalColors.RED}CHECK{TerminalColors.RESET}")

        if status:
                board_str += f"\nStatus: {' | '.join(status)}"

        # Add turn indicator
        turn = "White" if self.board.turn == chess.WHITE else "Black"
        turn_color = TerminalColors.BRIGHT_WHITE if self.board.turn == chess.WHITE else TerminalColors.WHITE
        board_str += f"\nTurn: {turn_color}{turn}{TerminalColors.RESET}"

        # Add move count
        board_str += f"\nMove: {self.move_index}"

        # Display
        TerminalControl.clear_screen()
        print(board_str)
        print("-" * 50)

    def run(self):
        """Main terminal loop"""
        
        while self.running:
            try:
                # Get input with custom prompt
                if self.in_database_mode:
                    prompt = f"{TerminalColors.MAGENTA}{self.username} /shchess/db>{TerminalColors.RESET}\n$ "
                else:
                    prompt = f"{TerminalColors.CYAN}{self.username} /shchess>{TerminalColors.RESET}\n$ "
                
                try:
                    cmd = InputHandler.get_input(prompt)
                except KeyboardInterrupt:
                    print(f"\n{TerminalColors.YELLOW}^C{TerminalColors.RESET}")
                    continue
                
                if not cmd.strip():
                    continue
                
                # Process command
                self.process_command(cmd.strip())
                
            except Exception as e:
                print(f"{TerminalColors.RED}Error: {e}{TerminalColors.RESET}")
    
    def process_command(self, command):
        """Process a command"""
        # Add to history
        self.history.append(command)
        self.history_index = len(self.history)
        
        # Check if in database mode
        if self.in_database_mode:
            if command.lower() in ['quit', 'exit']:
                self.in_database_mode = False
                self.show_welcome()
                return
            
            handled = self.database_explorer.process_command(command)
            if not handled:
                print(f"{TerminalColors.RED}Unknown database command{TerminalColors.RESET}")
            return
        
        # Split command
        parts = command.strip().split()
        if not parts:
            return
        
        cmd = parts[0].lower()
        args = parts[1:]
        
        # Check for chess move first (simple format)
        if CHESS_AVAILABLE and len(cmd) == 4 and cmd[0].isalpha() and cmd[1].isdigit() and cmd[2].isalpha() and cmd[3].isdigit():
            # Looks like UCI move (e2e4)
            result = self.make_move(cmd)
            if result:
                print(result)
            return
        
        # Check command dictionary
        if cmd in self.commands:
            result = self.commands[cmd](args)
            if result:
                print(result)
            return
        
        # Try as chess move (SAN)
        if CHESS_AVAILABLE:
            try:
                result = self.make_move(command)
                if result:
                    print(result)
                return
            except Exception:
                pass
        
        # Unknown command
        print(f"{TerminalColors.RED}Unknown command: {cmd}{TerminalColors.RESET}")
        print(f"{TerminalColors.YELLOW}Type 'help' for available commands{TerminalColors.RESET}")
    
    # ============================================================
    # COMMAND IMPLEMENTATIONS
    # ============================================================
    
    def cmd_help(self, args):
        """Show help"""
        help_text = f"""
{TerminalColors.CYAN}SHCHESS+ Commands:{TerminalColors.RESET}

{TerminalColors.YELLOW}Basic Commands:{TerminalColors.RESET}
  {TerminalColors.GREEN}help{TerminalColors.RESET}                 - Show this help
  {TerminalColors.GREEN}clear{TerminalColors.RESET}                - Clear terminal
  {TerminalColors.GREEN}quit/exit{TerminalColors.RESET}            - Exit program

{TerminalColors.YELLOW}Board Commands:{TerminalColors.RESET}
  {TerminalColors.GREEN}board{TerminalColors.RESET}                - Show current board
  {TerminalColors.GREEN}flip{TerminalColors.RESET}                 - Flip board perspective
  {TerminalColors.GREEN}fen{TerminalColors.RESET}                  - Show FEN string
  {TerminalColors.GREEN}legal{TerminalColors.RESET}                - Show legal moves
  {TerminalColors.GREEN}reset{TerminalColors.RESET}                - Reset to starting position
  {TerminalColors.GREEN}undo{TerminalColors.RESET}                 - Undo last move

{TerminalColors.YELLOW}Move Commands:{TerminalColors.RESET}
  {TerminalColors.GREEN}e2e4{TerminalColors.RESET}                 - Make move (UCI notation)
  {TerminalColors.GREEN}Nf3{TerminalColors.RESET}                  - Make move (SAN notation)

{TerminalColors.YELLOW}Navigation Commands:{TerminalColors.RESET}
  {TerminalColors.GREEN}first{TerminalColors.RESET}                - Go to first move
  {TerminalColors.GREEN}last{TerminalColors.RESET}                 - Go to last move
  {TerminalColors.GREEN}next{TerminalColors.RESET}                 - Next move
  {TerminalColors.GREEN}previous/prev{TerminalColors.RESET}        - Previous move

{TerminalColors.YELLOW}Game Management:{TerminalColors.RESET}
  {TerminalColors.GREEN}save [file]{TerminalColors.RESET}          - Save game to PGN file
  {TerminalColors.GREEN}load [file]{TerminalColors.RESET}          - Load game from PGN file
  {TerminalColors.GREEN}import [file]{TerminalColors.RESET}        - Import PGN or FEN file
  {TerminalColors.GREEN}type [file]{TerminalColors.RESET}          - Type PGN file with animation
  {TerminalColors.GREEN}stop{TerminalColors.RESET}                 - Stop typing animation

{TerminalColors.YELLOW}Analysis Commands:{TerminalColors.RESET}
  {TerminalColors.GREEN}analyze [depth]{TerminalColors.RESET}      - Analyze position
  {TerminalColors.GREEN}eval{TerminalColors.RESET}                 - Evaluate position
  {TerminalColors.GREEN}best{TerminalColors.RESET}                 - Find best move
  {TerminalColors.GREEN}tablebase / tb{TerminalColors.RESET}       - Query online endgame tablebase

{TerminalColors.YELLOW}Database Commands:{TerminalColors.RESET}
  {TerminalColors.GREEN}db{TerminalColors.RESET}                   - Enter database explorer
  {TerminalColors.GREEN}database{TerminalColors.RESET}             - Database operations

{TerminalColors.YELLOW}Other Commands:{TerminalColors.RESET}
  {TerminalColors.GREEN}history{TerminalColors.RESET}              - Show command history
  {TerminalColors.GREEN}showhistory{TerminalColors.RESET}          - Show game tree history with variations
  {TerminalColors.GREEN}deleteline [idx]{TerminalColors.RESET}     - Delete variation line
  {TerminalColors.GREEN}addcomment <text>{TerminalColors.RESET}    - Add comment to current position
  {TerminalColors.GREEN}makemainline [idx]{TerminalColors.RESET}   - Promote variation to mainline
  {TerminalColors.GREEN}$boardedit{TerminalColors.RESET}           - Launch visual board editor (curses)

{TerminalColors.CYAN}Examples:{TerminalColors.RESET}
  e2e4                 Play e2-e4
  analyze 20           Analyze to depth 20
  tablebase            Query endgame tablebase
  $boardedit           Launch board editor
  type game.pgn        Type PGN file
  first                Go to first move
  db                   Browse endgame database
"""
        return help_text
    
    def cmd_clear(self, args):
        """Clear terminal"""
        self.show_welcome()
        return ""
    
    def cmd_quit(self, args):
        """Exit program"""
        self.running = False
        print(f"{TerminalColors.YELLOW}Goodbye!{TerminalColors.RESET}")
        
        # Cleanup
        if hasattr(self, 'engine') and self.engine and hasattr(self.engine, 'close'):
            try:
                self.engine.close()
            except Exception:
                pass
        
        if hasattr(self, 'analyser') and self.analyser:
            try:
                self.analyser.stop_engine()
            except Exception:
                pass
        
        # Force exit
        sys.exit(0)
    
    def cmd_board(self, args):
        """Show board"""
        self.display_board()
        return ""
    
    def cmd_flip(self, args):
        """Flip board"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        self.flipped = not self.flipped
        self.display_board()
        return f"{TerminalColors.GREEN}Board flipped{TerminalColors.RESET}"
    
    def cmd_fen(self, args):
        """Show FEN"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        return f"FEN: {TerminalColors.CYAN}{self.board.fen()}{TerminalColors.RESET}"
    
    def cmd_legal(self, args):
        """Show legal moves"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        
        moves = list(self.board.legal_moves)
        move_strs = [self.board.san(m) for m in moves]
        
        output = [f"{TerminalColors.CYAN}Legal moves ({len(moves)}):{TerminalColors.RESET}"]
        
        # Group moves
        groups = {}
        for move_str in move_strs:
            prefix = move_str[0] if move_str[0].isalpha() else move_str[:2]
            if prefix not in groups:
                groups[prefix] = []
            groups[prefix].append(move_str)
        
        for prefix in sorted(groups.keys()):
            output.append(f"  {TerminalColors.YELLOW}{prefix}:{TerminalColors.RESET} {', '.join(groups[prefix])}")
        
        return "\n".join(output)

    def cmd_reset(self, args):
        """
        Reset board to starting position
        FIXED: Properly initializes all state variables
        """
        if not CHESS_AVAILABLE:
                return "python-chess not available"

        # Reset board
        self.board = chess.Board()
        self.start_board = chess.Board()
        
        # Reset game tree
        self.game = chess.pgn.Game()
        self.game_node = self.game
        
        # Reset move tracking
        self.mainline_moves = []
        self.move_index = 0
        self.start_fen = chess.STARTING_FEN
        
        self.display_board()
        return f"{TerminalColors.GREEN}Board reset to starting position{TerminalColors.RESET}"
        

    def cmd_undo(self, args):
        """Undo move"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        
        if not self.game_node.parent:
            return f"{TerminalColors.YELLOW}No moves to undo{TerminalColors.RESET}"
        
        self.game_node = self.game_node.parent
        self._rebuild_board_from_node()
        self.display_board()
        return f"{TerminalColors.GREEN}Move undone{TerminalColors.RESET}"
        
    def make_move(self, move_str):

        if not CHESS_AVAILABLE:
                return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"

        try:
                try:
                        move = self.board.parse_san(move_str)
                except ValueError:
                        move = chess.Move.from_uci(move_str.lower())
        except Exception:
                return f"{TerminalColors.RED}Invalid move: {move_str}{TerminalColors.RESET}"

        if move not in self.board.legal_moves:
                return f"{TerminalColors.RED}Illegal move: {move_str}{TerminalColors.RESET}"

        san = self.board.san(move)

        # -------------------------------------------------
        # CRITICAL FIX: handle FEN-origin games correctly
        # -------------------------------------------------

        if getattr(self, "game_node", None) is None:
                # No PGN yet → this is a FEN-origin game
                self.board.push(move)
                self.move_index += 1
        else:
                # PGN is authoritative
                self._advance_game_node(move)
                self._rebuild_board_from_node()
                self.move_index += 1

        output = f"{TerminalColors.GREEN}Move: {san}{TerminalColors.RESET}\n"

        if self.board.is_checkmate():
                output += f"\n{TerminalColors.RED}CHECKMATE!{TerminalColors.RESET}\n"
        elif self.board.is_stalemate():
                output += f"\n{TerminalColors.YELLOW}STALEMATE!{TerminalColors.RESET}\n"
        elif self.board.is_check():
                output += f"\n{TerminalColors.RED}CHECK!{TerminalColors.RESET}\n"

        self.display_board()
        return output
  
    def cmd_first(self, args):
        """Go to first move"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        self.move_navigator.first()
        return ""
    
    def cmd_last(self, args):
        """Go to last move"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        self.move_navigator.last()
        return ""
    
    def cmd_next(self, args):
        """Go to next move"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        self.move_navigator.next()
        return ""
    
    def cmd_prev(self, args):
        """Go to previous move"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        self.move_navigator.prev()
        return ""
    
    def cmd_save(self, args):
        """Save game to PGN file"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        
        filename = args[0] if args else f"game_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pgn"
        
        # Set game headers
        self.game.headers["Event"] = "SHChess Game"
        self.game.headers["Site"] = "SHChess Terminal"
        self.game.headers["Date"] = datetime.now().strftime("%Y.%m.%d")
        self.game.headers["Round"] = "1"
        self.game.headers["White"] = "Player"
        self.game.headers["Black"] = "Player"
        self.game.headers["Result"] = "*"
        
        try:
            with open(filename, 'w') as f:
                exporter = chess.pgn.FileExporter(f)
                self.game.accept(exporter)
            return f"{TerminalColors.GREEN}Game saved to: {filename}{TerminalColors.RESET}"
        except Exception as e:
            return f"{TerminalColors.RED}Error saving game: {e}{TerminalColors.RESET}"
    
    def cmd_load(self, args):
        if not args:
                print(f"{TerminalColors.RED}Usage: load <pgn_file>{TerminalColors.RESET}")
                return
        if isinstance(args, list):
            if not args:
                print(f"{TerminalColors.RED}Usage: load <pgn_file>{TerminalColors.RESET}")
                return
            path = args[0]
        else:
            path = args.strip()

        if not os.path.exists(path):
                print(f"{TerminalColors.RED}File not found: {path}{TerminalColors.RESET}")
                return

        try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        game = chess.pgn.read_game(f)

                if not game:
                        print(f"{TerminalColors.RED}No game found in PGN{TerminalColors.RESET}")
                        return

                # ✅ THIS is where game is defined
                self.load_game(game)

                print(f"{TerminalColors.GREEN}Game loaded successfully{TerminalColors.RESET}")
                self.display_board()

        except Exception as e:
                print(f"{TerminalColors.RED}Error loading game: {e}{TerminalColors.RESET}")

    def cmd_import(self, args):
        """Import PGN or FEN file"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        
        if not args:
            return f"{TerminalColors.RED}Usage: import <filename.pgn|filename.fen>{TerminalColors.RESET}"
        
        filename = args[0]
        
        if not os.path.exists(filename):
            return f"{TerminalColors.RED}File not found: {filename}{TerminalColors.RESET}"
        
        ext = os.path.splitext(filename)[1].lower()
        
        if ext == '.pgn':
            return self.cmd_load(args)
        elif ext in ['.fen', '.txt']:
            return self._import_fen(filename)
        else:
            return f"{TerminalColors.RED}Unsupported file type: {ext}{TerminalColors.RESET}"
    
    def _import_fen(self, filename):
        """
        Import FEN file
        FIXED: Properly initializes game tree from FEN position with correct board orientation
        """
        try:
                with open(filename, 'r') as f:
                        lines = f.readlines()
                
                fens = []
                for line in lines:
                        line = line.strip()
                        if line and not line.startswith('#'):
                                try:
                                        chess.Board(line)  # Validate
                                        fens.append(line)
                                except:
                                        pass
                
                if not fens:
                        return f"{TerminalColors.YELLOW}No valid FEN positions found{TerminalColors.RESET}"
                
                # Load first position
                fen_string = fens[0]
                
                # CRITICAL FIX: Create board from FEN - this handles orientation correctly
                self.board = chess.Board(fen_string)
                self.start_board = chess.Board(fen_string)
                
                # CRITICAL FIX: Properly initialize game from FEN
                self.game = chess.pgn.Game()
                
                # Set the starting FEN in game headers
                if fen_string != chess.STARTING_FEN:
                        self.game.headers["FEN"] = fen_string
                        self.game.headers["SetUp"] = "1"
                
                # Initialize game_node at root (board is already at the FEN position)
                self.game_node = self.game
                
                # Reset move tracking
                self.mainline_moves = []
                self.move_index = 0
                self.start_fen = fen_string
                
                self.display_board()
                
                if len(fens) > 1:
                        return f"{TerminalColors.GREEN}Imported {len(fens)} positions. Loaded first position.{TerminalColors.RESET}"
                else:
                        return f"{TerminalColors.GREEN}Imported position from FEN.{TerminalColors.RESET}"
                        
        except Exception as e:
                return f"{TerminalColors.RED}Error: {e}{TerminalColors.RESET}"



    def cmd_type(self, args):
        """Type PGN file"""
        if not args:
            return f"{TerminalColors.RED}Usage: type <filename.pgn> [--seconds N]{TerminalColors.RESET}"
        
        filename = args[0]
        seconds = 2.0
        
        if "--seconds" in args:
            i = args.index("--seconds")
            if i + 1 < len(args):
                try:
                    seconds = float(args[i + 1])
                except:
                    pass
        
        return self.file_typer.type_file(filename, seconds, True)
    
    def cmd_stop(self, args):
        """Stop typing"""
        return self.file_typer.stop_typing_command()

    
    def cmd_analyze(self, args):
        """Analyze position"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        
        if not self.engine.initialized:
            return f"{TerminalColors.RED}Engine not available{TerminalColors.RESET}"
        
        depth = 20
        if args:
            try:
                depth = int(args[0])
            except Exception:
                pass
        
        print(f"{TerminalColors.YELLOW}Analyzing position to depth {depth}...{TerminalColors.RESET}")
        
        # Run analysis
        result = self.engine.analyze(self.board, depth)
        
        if 'error' in result:
            return f"{TerminalColors.RED}Error: {result['error']}{TerminalColors.RESET}"
        
        output = []
        output.append(f"{TerminalColors.GREEN}Evaluation: {result['evaluation']}{TerminalColors.RESET}")
        if result.get('pv'):
            output.append(f"{TerminalColors.CYAN}Best line: {result['pv']}{TerminalColors.RESET}")
        
        return "\n".join(output)
    
    def cmd_eval(self, args):
        """Evaluate position"""
        return self.cmd_analyze(['20'])
    
    def cmd_best(self, args):
        """Find best move"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        
        if not self.engine.initialized:
            return f"{TerminalColors.RED}Engine not available{TerminalColors.RESET}"
        
        best_move = self.engine.get_best_move(self.board)
        if best_move:
            return f"{TerminalColors.GREEN}Best move: {best_move}{TerminalColors.RESET}"
        else:
            return f"{TerminalColors.RED}Could not find best move{TerminalColors.RESET}"
    
    def cmd_tablebase(self, args):
        """Query online endgame tablebase"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        
        if not REQUESTS_AVAILABLE:
            return f"{TerminalColors.RED}requests library not available. Install with: pip install requests{TerminalColors.RESET}"
        
        # Get FEN
        fen = self.board.fen()
        
        print(f"{TerminalColors.YELLOW}Querying online tablebase...{TerminalColors.RESET}")
        print(f"{TerminalColors.CYAN}FEN: {fen}{TerminalColors.RESET}")
        
        # Query tablebase
        result = OnlineTablebase.query(fen)
        
        if "error" in result:
            return f"{TerminalColors.RED}Error: {result['error']}{TerminalColors.RESET}"
        
        # Format output
        output = [f"{TerminalColors.GREEN}Tablebase Result:{TerminalColors.RESET}"]
        
        # Check source
        source = result.get("_source", result.get("source", "unknown"))
        output.append(f"{TerminalColors.CYAN}Source: {source}{TerminalColors.RESET}")
        
        # Lokasoft response (raw text)
        if source == "lokasoft":
            output.append(f"{TerminalColors.YELLOW}Response:{TerminalColors.RESET}")
            output.append(result.get("raw", "No data"))
            return "\n".join(output)
        
        # Lichess/Syzygy response (JSON)
        # DTZ (distance to zeroing)
        if "dtz" in result:
            dtz = result["dtz"]
            if dtz is None:
                output.append(f"{TerminalColors.YELLOW}DTZ: Position not in tablebase{TerminalColors.RESET}")
            elif dtz == 0:
                output.append(f"{TerminalColors.YELLOW}DTZ: 0 (drawn position){TerminalColors.RESET}")
            elif dtz > 0:
                output.append(f"{TerminalColors.GREEN}DTZ: +{dtz} (winning for white){TerminalColors.RESET}")
            else:
                output.append(f"{TerminalColors.RED}DTZ: {dtz} (winning for black){TerminalColors.RESET}")
        
        # Category
        if "category" in result:
            cat = result["category"]
            output.append(f"{TerminalColors.CYAN}Category: {cat}{TerminalColors.RESET}")
        
        # Moves
        if "moves" in result and result["moves"]:
            output.append(f"\n{TerminalColors.CYAN}Available moves:{TerminalColors.RESET}")
            for move_data in result["moves"][:10]:  # Show first 10 moves
                uci = move_data.get("uci", "?")
                san_move = move_data.get("san", uci)
                dtz_move = move_data.get("dtz")
                
                # Color code by result
                if dtz_move is None:
                    color = TerminalColors.YELLOW
                    dtz_str = "?"
                elif dtz_move == 0:
                    color = TerminalColors.YELLOW
                    dtz_str = "0"
                elif dtz_move > 0:
                    color = TerminalColors.GREEN
                    dtz_str = f"+{dtz_move}"
                else:
                    color = TerminalColors.RED
                    dtz_str = f"{dtz_move}"
                
                output.append(f"  {color}{san_move:10} DTZ: {dtz_str}{TerminalColors.RESET}")
        
        # WDL (Win/Draw/Loss)
        if "wdl" in result:
            wdl = result["wdl"]
            if wdl == -2:
                wdl_str = "Loss"
                color = TerminalColors.RED
            elif wdl == -1:
                wdl_str = "Blessed loss (50-move rule)"
                color = TerminalColors.YELLOW
            elif wdl == 0:
                wdl_str = "Draw"
                color = TerminalColors.YELLOW
            elif wdl == 1:
                wdl_str = "Cursed win (50-move rule)"
                color = TerminalColors.YELLOW
            elif wdl == 2:
                wdl_str = "Win"
                color = TerminalColors.GREEN
            else:
                wdl_str = f"Unknown ({wdl})"
                color = TerminalColors.WHITE
            
            output.append(f"\n{TerminalColors.CYAN}WDL: {color}{wdl_str}{TerminalColors.RESET}")
        
        return "\n".join(output)
    
    def cmd_db(self, args):
        """Enter database explorer"""
        self.in_database_mode = True
        
        # Clear screen
        TerminalControl.clear_screen()
        
        # Show database header
        print(f"{TerminalColors.MAGENTA}{'='*60}{TerminalColors.RESET}")
        print(f"{TerminalColors.BRIGHT_MAGENTA}DATABASE EXPLORER{TerminalColors.RESET}")
        print(f"{TerminalColors.MAGENTA}{'='*60}{TerminalColors.RESET}")
        print(f"{TerminalColors.YELLOW}Type 'help' for commands, 'quit' to exit{TerminalColors.RESET}")
        
        # Run database explorer
        if self.database_explorer.run():
            return f"{TerminalColors.GREEN}Entered database mode{TerminalColors.RESET}"
        else:
            self.in_database_mode = False
            return f"{TerminalColors.RED}Failed to enter database mode{TerminalColors.RESET}"
    
    def cmd_database(self, args):
        """Database operations"""
        return self.cmd_db(args)
    
    def cmd_history(self, args):
        """Show command history"""
        if not self.history:
            return f"{TerminalColors.YELLOW}No command history{TerminalColors.RESET}"
        
        output = [f"{TerminalColors.CYAN}Command History:{TerminalColors.RESET}"]
        for i, cmd in enumerate(self.history[-20:], 1):
            output.append(f"  {TerminalColors.YELLOW}{i:2}{TerminalColors.RESET}. {cmd}")
        
        return "\n".join(output)
    
    def cmd_showhistory(self, args):
        """Show game tree history with variations and comments"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        
        if not self.game.variations:
            return f"{TerminalColors.YELLOW}No moves yet{TerminalColors.RESET}"
        
        output = [f"{TerminalColors.CYAN}Game History:{TerminalColors.RESET}"]
        output.append(f"{TerminalColors.YELLOW}Commands: deleteline [idx], addcomment <text>, makemainline [idx]{TerminalColors.RESET}")
        output.append("")
        
        def walk(node, indent=0):
            base_board = node.board()

            for idx, var in enumerate(node.variations):
                    # SAN must be computed on parent board
                    try:
                                san = base_board.san(var.move)
                    except Exception:
                                san = str(var.move)

                    move_no = base_board.fullmove_number
                    turn = "." if base_board.turn == chess.WHITE else "..."
                    prefix = "  " * indent

                    # Mark current node
                    mark = ""
                    if var == self.game_node:
                                mark = f" {TerminalColors.BRIGHT_YELLOW}<= CURRENT{TerminalColors.RESET}"
                    
                    # Add variation index if not mainline (idx > 0)
                    var_marker = f" [{idx}]" if idx > 0 else ""
                    
                    # Main move line
                    output.append(f"{prefix}{TerminalColors.YELLOW}{move_no}{turn}{TerminalColors.RESET} {san}{var_marker}{mark}")
                    
                    # Show comment if exists
                    if var.comment:
                        comment_lines = var.comment.split('\n')
                        for comment_line in comment_lines:
                            output.append(f"{prefix}  {TerminalColors.CYAN}; {comment_line}{TerminalColors.RESET}")

                    # Recurse into that variation (var.board() is already advanced)
                    walk(var, indent + 1)

        
        walk(self.game)
        return "\n".join(output)
    
    def cmd_deleteline(self, args):
        """Delete a variation line: deleteline [index]"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        
        if not self.game_node.parent:
            return f"{TerminalColors.RED}Cannot delete root position{TerminalColors.RESET}"
        
        parent = self.game_node.parent
        
        # If no args, delete current line
        if not args:
            # Delete current node
            parent.variations.remove(self.game_node)
            # Move to parent
            self.game_node = parent
            self.board = parent.board().copy()
            self.display_board()
            return f"{TerminalColors.GREEN}Deleted current variation line{TerminalColors.RESET}"
        
        # Otherwise delete by index
        try:
            idx = int(args[0])
            if idx < 0 or idx >= len(parent.variations):
                return f"{TerminalColors.RED}Invalid variation index: {idx}{TerminalColors.RESET}"
            
            deleted_var = parent.variations[idx]
            parent.variations.remove(deleted_var)
            
            # If we deleted the current node, move to parent
            if deleted_var == self.game_node:
                self.game_node = parent
                self.board = parent.board().copy()
            
            self.display_board()
            return f"{TerminalColors.GREEN}Deleted variation {idx}{TerminalColors.RESET}"
        except (ValueError, IndexError):
            return f"{TerminalColors.RED}Usage: deleteline [index]{TerminalColors.RESET}"
    
    def cmd_addcomment(self, args):
        """Add comment to current position: addcomment <text>"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        
        if not args:
            return f"{TerminalColors.RED}Usage: addcomment <comment text>{TerminalColors.RESET}"
        
        comment_text = " ".join(args)
        self.game_node.comment = comment_text
        return f"{TerminalColors.GREEN}Added comment: {comment_text}{TerminalColors.RESET}"
    
    def cmd_makemainline(self, args):
        """Promote variation to mainline: makemainline [index]"""
        if not CHESS_AVAILABLE:
            return f"{TerminalColors.RED}python-chess not available{TerminalColors.RESET}"
        
        parent = self.game_node.parent
        if not parent:
            return f"{TerminalColors.YELLOW}Already at root position{TerminalColors.RESET}"
        
        # If no args, promote current node
        if not args:
            # Remove current node and insert at position 0
            parent.variations.remove(self.game_node)
            parent.variations.insert(0, self.game_node)
            self.display_board()
            return f"{TerminalColors.GREEN}Promoted current line to mainline{TerminalColors.RESET}"
        
        # Otherwise promote by index
        try:
            idx = int(args[0])
            if idx < 0 or idx >= len(parent.variations):
                return f"{TerminalColors.RED}Invalid variation index: {idx}{TerminalColors.RESET}"
            
            var_to_promote = parent.variations[idx]
            parent.variations.remove(var_to_promote)
            parent.variations.insert(0, var_to_promote)
            
            # Update game_node if it was the promoted one
            if var_to_promote == self.game_node:
                pass  # Already there
            
            self.display_board()
            return f"{TerminalColors.GREEN}Promoted variation {idx} to mainline{TerminalColors.RESET}"
        except (ValueError, IndexError):
            return f"{TerminalColors.RED}Usage: makemainline [index]{TerminalColors.RESET}"
    
    def cmd_boardedit(self, args=""):
        try:
                # Use current board if available
                board = None
                if hasattr(self, "board") and self.board:
                        board = self.board

                # Run board editor with curses wrapper
                def run_editor(stdscr):
                        from BoardEditorFix import App as BoardEditor
                        editor = BoardEditor(
                                stdscr=stdscr,
                                board=board.copy() if board else chess.Board(),
                                save_path=None,
                                engine_path="./stockfish"
                        )
                        try:
                                editor.loop()
                        finally:
                                editor.close()
                        return editor.board

                # Run editor
                if not CURSES_AVAILABLE:
                        print(f"{TerminalColors.RED}curses not available{TerminalColors.RESET}")
                        return
                        
                new_board = curses.wrapper(run_editor)

                # CRITICAL: Reset entire game after board edit
                if new_board:
                        self.board = new_board
                        
                        # Reset game completely (like database load)
                        self.game = chess.pgn.Game()
                        self.game.headers["FEN"] = self.board.fen()
                        self.game.headers["SetUp"] = "1"
                        self.game_node = self.game
                        self.mainline_moves = []
                        self.move_index = 0
                        self.start_board = self.board.copy()

                print(f"{TerminalColors.GREEN}Returned from board editor{TerminalColors.RESET}")
                print(f"{TerminalColors.YELLOW}Game history has been reset{TerminalColors.RESET}")
                self.display_board()

        except Exception as e:
                print(f"{TerminalColors.RED}Board editor error: {e}{TerminalColors.RESET}")
                import traceback
                traceback.print_exc()

    def setup_readline(self):
        """Setup command history with up/down arrows"""
        try:
            import readline
            histfile = os.path.join(os.path.expanduser("~"), ".shchess_history")
            try:
                readline.read_history_file(histfile)
            except Exception:
                pass
            import atexit
            atexit.register(lambda: readline.write_history_file(histfile))
            readline.set_history_length(1000)
            print(f"{TerminalColors.GREEN}Command history enabled (↑/↓){TerminalColors.RESET}")
        except ImportError:
            pass

    def setup_signal_handlers(self):
        """Setup CTRL+C handler"""
        def signal_handler(sig, frame):
            print(f"\n{TerminalColors.YELLOW}CTRL+C detected - Stopping...{TerminalColors.RESET}")
            if hasattr(self, 'engine') and self.engine and self.engine.initialized:
                self.engine.close()
            if hasattr(self, 'file_typer') and self.file_typer.typing_active:
                self.file_typer.stop_typing_command()

            if hasattr(self, 'move_navigator'):
                if hasattr(self.move_navigator, 'macbeth_analysis') and self.move_navigator.macbeth_analysis:
                    self.move_navigator.macbeth_analysis.stop()
            sys.exit(0)
        signal.signal(signal.SIGINT, signal_handler)

    def cmd_macbeth(self, args):
        depth = int(args[0]) if args else 24
        if not self.game:
                return "No game loaded\n"

        print(f"{TerminalColors.CYAN}Starting Macbeth analysis at depth {depth}...{TerminalColors.RESET}")

        # Build a board with ALL moves from the game
        # Start from the game's initial position
        analysis_board = self.board 
        
        # Get all mainline moves from the game
        moves = list(self.game.mainline_moves())
        
        if not moves:
            return f"{TerminalColors.RED}No moves to analyze - game is empty{TerminalColors.RESET}\n"
        
        # Push all moves onto the board
        for move in moves:
            analysis_board.push(move)
        
        print(f"{TerminalColors.CYAN}Analyzing {len(moves)} moves...{TerminalColors.RESET}")

        # Create & run Macbeth directly
        self.macbeth = MacbethAnalysis(self.game, depth=depth, engine_path="./stockfish")
        
        # Set macbeth analysis on move navigator so it can display results during navigation
        if hasattr(self, 'move_navigator'):
            self.move_navigator.set_macbeth_analysis(self.macbeth)
        
        ok = self.macbeth.run_async(analysis_board, progress_callback=None)
        return "Starting Macbeth analysis...\n" if ok else "Engine not available\n"
# ============================================================
# MAIN FUNCTION
# ============================================================

def main():
    """Main entry point"""
    print(f"{TerminalColors.YELLOW}Starting SHCHESS+ Terminal...{TerminalColors.RESET}")
    
    if not CHESS_AVAILABLE:
        print(f"\n{TerminalColors.RED}WARNING: python-chess is not installed!{TerminalColors.RESET}")
        print(f"{TerminalColors.YELLOW}The program will run in limited mode.{TerminalColors.RESET}")
        print(f"{TerminalColors.YELLOW}Most chess features will not work.{TerminalColors.RESET}\n")
    
    # Create and run terminal
    terminal = ChessTerminal()
    
    try:
        terminal.run()
    except KeyboardInterrupt:
        print(f"\n{TerminalColors.YELLOW}Goodbye!{TerminalColors.RESET}")
    except Exception as e:
        print(f"\n{TerminalColors.RED}Fatal error: {e}{TerminalColors.RESET}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
