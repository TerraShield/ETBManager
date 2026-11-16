"""Petit sélecteur Windows plein écran; transmet le rectangle au serveur ETB Manager."""
from __future__ import annotations

import argparse
import json
import tkinter as tk
import urllib.request


class RegionSelector:
    def __init__(self, port: int):
        self.port = port
        self.start = None
        self.rect = None
        self.root = tk.Tk()
        self.root.attributes("-topmost", True)
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-alpha", 0.28)
        self.root.configure(bg="black")
        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0, cursor="cross")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            30, 30, anchor="nw",
            text="Clique et fais glisser pour sélectionner une zone autour d'une lampe.\n"
                 "Évite le HUD, le viseur et les éléments mobiles. Échap pour annuler.",
            fill="white", font=("Segoe UI", 16, "bold")
        )
        self.canvas.bind("<ButtonPress-1>", self.down)
        self.canvas.bind("<B1-Motion>", self.move)
        self.canvas.bind("<ButtonRelease-1>", self.up)
        self.root.bind("<Escape>", lambda _event: self.root.destroy())
        self.root.focus_force()

    def down(self, event):
        self.start = (event.x_root, event.y_root)
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#00ff88", width=3)

    def move(self, event):
        if self.start and self.rect:
            x0 = self.start[0] - self.root.winfo_rootx()
            y0 = self.start[1] - self.root.winfo_rooty()
            self.canvas.coords(self.rect, x0, y0, event.x, event.y)

    def up(self, event):
        if not self.start:
            return
        x1, y1 = self.start
        x2, y2 = event.x_root, event.y_root
        region = {
            "left": min(x1, x2), "top": min(y1, y2),
            "width": abs(x2 - x1), "height": abs(y2 - y1),
        }
        if region["width"] < 12 or region["height"] < 12:
            return
        try:
            body = json.dumps(region).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/garage/region",
                data=body, headers={"Content-Type": "application/json"}, method="POST"
            )
            urllib.request.urlopen(request, timeout=2).read()
        except Exception as exc:
            print(f"Impossible d'envoyer la zone au gestionnaire : {exc}")
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    RegionSelector(args.port).run()
