"""Compose all PNG chart fixtures into one contact-sheet image for quick review."""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
ORDER = ["bar_sales", "line_trend", "pie_share", "grouped_bar",
         "scatter_corr", "stacked_bar", "dashboard"]
imgs = [(n, FIX / f"{n}.png") for n in ORDER if (FIX / f"{n}.png").exists()]

ncols = 2
nrows = (len(imgs) + ncols - 1) // ncols
fig, axes = plt.subplots(nrows, ncols, figsize=(14, 4.2 * nrows))
axes = axes.ravel()
for ax, (name, path) in zip(axes, imgs):
    ax.imshow(mpimg.imread(path))
    ax.set_title(f"{name}.png", fontsize=12, fontweight="bold")
    ax.axis("off")
for ax in axes[len(imgs):]:
    ax.axis("off")
fig.suptitle("Unsiloed probe — chart fixtures (7 PNGs; report.pdf reuses line_trend + stacked_bar)",
             fontsize=14, fontweight="bold")
fig.tight_layout(rect=[0, 0, 1, 0.98])
out = HERE / "out" / "montage.png"
out.parent.mkdir(exist_ok=True)
fig.savefig(out, dpi=110, bbox_inches="tight")
print(out)
