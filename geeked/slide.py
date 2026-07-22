import requests

from geeked.dddd_client import get_dddd_client


class SlideSolver:
    def __init__(self, puzzle_piece, background):
        self.puzzle_piece = puzzle_piece
        self.background = background

    @staticmethod
    def load_image(url: str) -> bytes:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.content

    def find_puzzle_piece_position(self):
        client = get_dddd_client()
        return float(client.slide_gap(self.puzzle_piece, self.background))
