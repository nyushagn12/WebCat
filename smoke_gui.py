from pathlib import Path
import tkinter as tk
from safehtmltk.css import Stylesheet
from safehtmltk.parser import parse_document
from safehtmltk.renderer import Renderer

root = tk.Tk()
root.geometry("1100x760")
source = Path(__file__).parent / "examples" / "css-showcase.html"
doc, css, _ = parse_document(source.read_text(encoding="utf-8"))
r = Renderer(root, doc, Stylesheet(css), lambda event: print(event))
root.after(3000, root.destroy)
root.mainloop()
