from __future__ import annotations

import argparse
import gc
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFont


DEFAULT_EXTENSIONS = (".tiff", ".tif", ".png", ".bmp")
DEFAULT_CHUNK_ROWS = 64
EPSILON = 1e-12
ELEMENT_PREVIEW_WIDTH = 1100
OVERVIEW_PANEL_WIDTH = 320
TITLE_BAR_HEIGHT = 30
COLORBAR_WIDTH = 56
PANEL_PADDING = 10


@dataclass(frozen=True)
class Measurement:
    generator_token: str
    analyzer_token: str
    generator_angle: float
    analyzer_angle: float
    path: Path


def linear_stokes(angle_deg: float) -> np.ndarray:
    theta = np.deg2rad(angle_deg)
    return np.array(
        [1.0, np.cos(2.0 * theta), np.sin(2.0 * theta)],
        dtype=np.float64,
    )


def read_image_as_float(path: Path) -> np.ndarray:
    image = np.asarray(Image.open(path))
    if image.ndim == 3:
        image = image[..., :3]
        image = image.mean(axis=2)
    return image.astype(np.float32, copy=False)


def parse_measurement_filename(path: Path) -> Measurement:
    parts = path.stem.split("_")
    if len(parts) != 2:
        raise ValueError(
            f"Invalid measurement filename '{path.name}'. "
            "Expected '<generator_angle>_<analyzer_angle>'."
        )

    generator_token, analyzer_token = parts
    return Measurement(
        generator_token=generator_token,
        analyzer_token=analyzer_token,
        generator_angle=float(generator_token),
        analyzer_angle=float(analyzer_token),
        path=path,
    )


def choose_preferred_measurements(
    image_files: Iterable[Path], ext_priority: tuple[str, ...]
) -> list[Measurement]:
    priority_map = {suffix.lower(): idx for idx, suffix in enumerate(ext_priority)}
    best_by_pair: dict[tuple[str, str], tuple[int, Measurement]] = {}

    for path in image_files:
        try:
            measurement = parse_measurement_filename(path)
        except ValueError:
            continue

        priority = priority_map.get(path.suffix.lower(), len(priority_map))
        pair_key = (measurement.generator_token, measurement.analyzer_token)

        previous = best_by_pair.get(pair_key)
        if previous is None or priority < previous[0]:
            best_by_pair[pair_key] = (priority, measurement)

    measurements = [entry[1] for entry in best_by_pair.values()]
    measurements.sort(key=lambda item: (item.analyzer_angle, item.generator_angle, item.path.name))
    return measurements


def discover_measurements(dataset_dir: Path, ext_priority: tuple[str, ...]) -> list[Measurement]:
    image_files = [
        path
        for path in dataset_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {ext.lower() for ext in ext_priority}
    ]
    measurements = choose_preferred_measurements(image_files, ext_priority)

    if len(measurements) < 9:
        raise ValueError(
            f"{dataset_dir} only has {len(measurements)} valid measurement images. "
            "At least 9 are required to solve a 3x3 Mueller matrix."
        )

    return measurements


def build_system_matrix(measurements: list[Measurement]) -> np.ndarray:
    rows = []
    for measurement in measurements:
        analyzer = linear_stokes(measurement.analyzer_angle)
        generator = linear_stokes(measurement.generator_angle)
        rows.append(np.kron(analyzer, generator))
    return np.asarray(rows, dtype=np.float64)


def discover_dataset_dirs(input_path: Path, ext_priority: tuple[str, ...]) -> list[Path]:
    if not input_path.exists():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")

    if input_path.is_file():
        raise ValueError("Please pass a folder path, not a file path.")

    direct_measurements = choose_preferred_measurements(
        (
            path
            for path in input_path.iterdir()
            if path.is_file() and path.suffix.lower() in {ext.lower() for ext in ext_priority}
        ),
        ext_priority,
    )
    if direct_measurements:
        return [input_path]

    dataset_dirs = []
    for child in sorted(input_path.iterdir()):
        if not child.is_dir():
            continue
        child_measurements = choose_preferred_measurements(
            (
                path
                for path in child.iterdir()
                if path.is_file() and path.suffix.lower() in {ext.lower() for ext in ext_priority}
            ),
            ext_priority,
        )
        if child_measurements:
            dataset_dirs.append(child)

    if not dataset_dirs:
        raise FileNotFoundError(
            f"No measurement folders were found under {input_path}. "
            "Expected files named like '<generator_angle>_<analyzer_angle>.tiff'."
        )

    return dataset_dirs


def create_measurement_stack(
    measurements: list[Measurement],
    output_dir: Path,
    dark_image_path: Path | None,
) -> tuple[Path, tuple[int, int]]:
    first_image = read_image_as_float(measurements[0].path)
    height, width = first_image.shape

    dark_image = None
    if dark_image_path is not None:
        dark_image = read_image_as_float(dark_image_path)
        if dark_image.shape != (height, width):
            raise ValueError(
                f"Dark image shape mismatch: {dark_image.shape} vs {(height, width)}"
            )

    stack_path = output_dir / "_measurement_stack.npy"
    stack_mm = np.lib.format.open_memmap(
        stack_path,
        mode="w+",
        dtype=np.float32,
        shape=(len(measurements), height, width),
    )

    for index, measurement in enumerate(measurements):
        image = read_image_as_float(measurement.path)
        if image.shape != (height, width):
            raise ValueError(
                f"Image shape mismatch for {measurement.path}: "
                f"{image.shape} vs {(height, width)}"
            )

        if dark_image is not None:
            image = np.clip(image - dark_image, 0.0, None)

        stack_mm[index] = image
        print(
            f"  Loaded {index + 1:02d}/{len(measurements):02d}: "
            f"{measurement.path.name}"
        )

    del stack_mm
    return stack_path, (height, width)


def solve_mueller_image_chunked(
    measurement_stack_path: Path,
    system_matrix_pinv: np.ndarray,
    output_dir: Path,
    chunk_rows: int,
    eps: float,
) -> tuple[Path, Path, np.ndarray, int]:
    measurement_stack = np.load(measurement_stack_path, mmap_mode="r")
    num_measurements, height, width = measurement_stack.shape

    raw_output_path = output_dir / "M_img.npy"
    norm_output_path = output_dir / "M_norm.npy"

    raw_mm = np.lib.format.open_memmap(
        raw_output_path,
        mode="w+",
        dtype=np.float32,
        shape=(height, width, 3, 3),
    )
    norm_mm = np.lib.format.open_memmap(
        norm_output_path,
        mode="w+",
        dtype=np.float32,
        shape=(height, width, 3, 3),
    )

    normalized_sum = np.zeros((9,), dtype=np.float64)
    valid_pixel_count = 0

    for y0 in range(0, height, chunk_rows):
        y1 = min(y0 + chunk_rows, height)
        chunk_height = y1 - y0

        chunk = np.asarray(measurement_stack[:, y0:y1, :], dtype=np.float64)
        b_matrix = chunk.reshape(num_measurements, -1)
        m_vector = system_matrix_pinv @ b_matrix
        raw_chunk = m_vector.T.reshape(chunk_height, width, 3, 3)

        raw_mm[y0:y1] = raw_chunk.astype(np.float32)

        m00 = raw_chunk[:, :, 0, 0]
        valid_mask = np.abs(m00) > eps
        norm_chunk = np.zeros_like(raw_chunk, dtype=np.float32)

        if np.any(valid_mask):
            normalized_values = raw_chunk[valid_mask] / m00[valid_mask][:, None, None]
            norm_chunk[valid_mask] = normalized_values.astype(np.float32)
            normalized_sum += normalized_values.reshape(-1, 9).sum(axis=0)
            valid_pixel_count += normalized_values.shape[0]

        norm_mm[y0:y1] = norm_chunk
        print(
            f"  Solved rows {y0:04d}-{y1 - 1:04d} / {height - 1:04d}"
        )

    del raw_mm
    del norm_mm
    del measurement_stack

    if valid_pixel_count == 0:
        raise ValueError("No valid pixels were found after m00 normalization.")

    normalized_mean = (normalized_sum / valid_pixel_count).reshape(3, 3)
    return raw_output_path, norm_output_path, normalized_mean, valid_pixel_count


def jet_colormap(normalized_values: np.ndarray) -> np.ndarray:
    red = np.clip(1.5 - np.abs(4.0 * normalized_values - 3.0), 0.0, 1.0)
    green = np.clip(1.5 - np.abs(4.0 * normalized_values - 2.0), 0.0, 1.0)
    blue = np.clip(1.5 - np.abs(4.0 * normalized_values - 1.0), 0.0, 1.0)
    return np.stack([red, green, blue], axis=-1)


def compute_display_range(
    array: np.ndarray,
    fixed_range: tuple[float, float] | None,
) -> tuple[float, float]:
    if fixed_range is not None:
        return float(fixed_range[0]), float(fixed_range[1])

    finite_values = array[np.isfinite(array)]
    if finite_values.size == 0:
        return 0.0, 1.0

    value_min = float(finite_values.min())
    value_max = float(finite_values.max())
    if np.isclose(value_min, value_max):
        value_max = value_min + 1.0
    return value_min, value_max


def resize_with_aspect(image: Image.Image, target_width: int) -> Image.Image:
    if image.width <= target_width:
        return image.copy()

    target_height = max(1, round(image.height * target_width / image.width))
    return image.resize((target_width, target_height), Image.Resampling.BILINEAR)


def make_colorbar(height: int, value_min: float, value_max: float) -> Image.Image:
    gradient = np.linspace(1.0, 0.0, height, dtype=np.float32)[:, None]
    gradient_rgb = (jet_colormap(gradient) * 255.0).astype(np.uint8)
    colorbar = Image.fromarray(np.repeat(gradient_rgb, 24, axis=1), mode="RGB")

    canvas = Image.new("RGB", (COLORBAR_WIDTH, height), "white")
    canvas.paste(colorbar, (0, 0))

    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    draw.text((28, 0), f"{value_max:.3g}", fill="black", font=font)
    draw.text((28, max(0, height - 12)), f"{value_min:.3g}", fill="black", font=font)
    return canvas


def render_heatmap_panel(
    array: np.ndarray,
    title: str,
    fixed_range: tuple[float, float] | None,
    target_width: int,
) -> Image.Image:
    display = np.asarray(array, dtype=np.float32)
    value_min, value_max = compute_display_range(display, fixed_range)
    normalized = np.clip((display - value_min) / (value_max - value_min), 0.0, 1.0)
    rgb = (jet_colormap(normalized) * 255.0).astype(np.uint8)
    rgb[~np.isfinite(display)] = 0

    heatmap = Image.fromarray(rgb, mode="RGB")
    heatmap = resize_with_aspect(heatmap, target_width)
    colorbar = make_colorbar(heatmap.height, value_min, value_max)

    panel_width = heatmap.width + colorbar.width + PANEL_PADDING * 3
    panel_height = heatmap.height + TITLE_BAR_HEIGHT + PANEL_PADDING * 2
    panel = Image.new("RGB", (panel_width, panel_height), "white")

    draw = ImageDraw.Draw(panel)
    font = ImageFont.load_default()
    draw.rectangle((0, 0, panel_width, TITLE_BAR_HEIGHT), fill=(240, 240, 240))
    draw.text((PANEL_PADDING, 8), title, fill="black", font=font)

    top = TITLE_BAR_HEIGHT + PANEL_PADDING
    panel.paste(heatmap, (PANEL_PADDING, top))
    panel.paste(colorbar, (heatmap.width + PANEL_PADDING * 2, top))
    return panel


def save_element_maps(
    matrix_path: Path,
    out_dir: Path,
    prefix: str,
    cmap: str,
    fixed_range: tuple[float, float] | None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    matrix_mm = np.load(matrix_path, mmap_mode="r")

    for row in range(3):
        for col in range(3):
            element = np.asarray(matrix_mm[:, :, row, col], dtype=np.float32)
            np.save(out_dir / f"{prefix}_{row}{col}.npy", element)
            Image.fromarray(element).save(out_dir / f"{prefix}_{row}{col}.tiff")
            panel = render_heatmap_panel(
                element,
                title=f"{prefix}[{row}{col}]",
                fixed_range=fixed_range,
                target_width=ELEMENT_PREVIEW_WIDTH,
            )
            panel.save(out_dir / f"{prefix}_{row}{col}.png")


def save_3x3_overview(
    matrix_path: Path,
    out_path: Path,
    title: str,
    cmap: str,
    fixed_range: tuple[float, float] | None,
) -> None:
    matrix_mm = np.load(matrix_path, mmap_mode="r")
    panels = []
    for row in range(3):
        row_panels = []
        for col in range(3):
            element = np.asarray(matrix_mm[:, :, row, col], dtype=np.float32)
            row_panels.append(
                render_heatmap_panel(
                    element,
                    title=f"M[{row}{col}]",
                    fixed_range=fixed_range,
                    target_width=OVERVIEW_PANEL_WIDTH,
                )
            )
        panels.append(row_panels)

    font = ImageFont.load_default()
    panel_width = max(panel.width for row_panels in panels for panel in row_panels)
    panel_height = max(panel.height for row_panels in panels for panel in row_panels)
    title_height = 42

    canvas_width = panel_width * 3 + PANEL_PADDING * 4
    canvas_height = panel_height * 3 + title_height + PANEL_PADDING * 4
    canvas = Image.new("RGB", (canvas_width, canvas_height), "white")

    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, canvas_width, title_height), fill=(225, 225, 225))
    draw.text((PANEL_PADDING, 14), title, fill="black", font=font)

    for row in range(3):
        for col in range(3):
            panel = panels[row][col]
            left = PANEL_PADDING + col * (panel_width + PANEL_PADDING)
            top = title_height + PANEL_PADDING + row * (panel_height + PANEL_PADDING)
            canvas.paste(panel, (left, top))

    canvas.save(out_path)


def write_summary_json(summary_path: Path, payload: dict) -> None:
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def analyze_dataset(
    dataset_dir: Path,
    output_root: Path,
    ext_priority: tuple[str, ...],
    dark_image_path: Path | None,
    chunk_rows: int,
    save_individual_maps: bool,
    save_overview_images: bool,
    keep_measurement_stack: bool,
) -> dict:
    output_dir = output_root / dataset_dir.name
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== Analyzing dataset: {dataset_dir.name} ===")
    measurements = discover_measurements(dataset_dir, ext_priority)
    system_matrix = build_system_matrix(measurements)
    matrix_rank = int(np.linalg.matrix_rank(system_matrix))

    if matrix_rank < 9:
        raise ValueError(
            f"System matrix rank is {matrix_rank}, which is not enough to solve a 3x3 Mueller matrix."
        )

    stack_path, (height, width) = create_measurement_stack(
        measurements=measurements,
        output_dir=output_dir,
        dark_image_path=dark_image_path,
    )

    raw_path, norm_path, normalized_mean, valid_pixel_count = solve_mueller_image_chunked(
        measurement_stack_path=stack_path,
        system_matrix_pinv=np.linalg.pinv(system_matrix),
        output_dir=output_dir,
        chunk_rows=chunk_rows,
        eps=EPSILON,
    )

    if save_individual_maps:
        save_element_maps(
            raw_path,
            out_dir=output_dir / "raw_elements",
            prefix="M",
            cmap="jet",
            fixed_range=None,
        )
        save_element_maps(
            norm_path,
            out_dir=output_dir / "normalized_elements",
            prefix="Mnorm",
            cmap="jet",
            fixed_range=(-1.0, 1.0),
        )

    if save_overview_images:
        save_3x3_overview(
            raw_path,
            out_path=output_dir / "Mueller_3x3_raw_overview.png",
            title=f"{dataset_dir.name} - 3x3 Mueller Matrix (Raw)",
            cmap="jet",
            fixed_range=None,
        )
        save_3x3_overview(
            norm_path,
            out_path=output_dir / "Mueller_3x3_normalized_overview.png",
            title=f"{dataset_dir.name} - 3x3 Mueller Matrix (Normalized by m00)",
            cmap="jet",
            fixed_range=(-1.0, 1.0),
        )

    raw_mm = np.load(raw_path, mmap_mode="r")
    norm_mm = np.load(norm_path, mmap_mode="r")
    center_y = height // 2
    center_x = width // 2

    center_raw = np.asarray(raw_mm[center_y, center_x], dtype=np.float64)
    center_norm = np.asarray(norm_mm[center_y, center_x], dtype=np.float64)

    summary = {
        "dataset_name": dataset_dir.name,
        "input_dir": str(dataset_dir.resolve()),
        "output_dir": str(output_dir.resolve()),
        "image_shape": [height, width],
        "measurement_count": len(measurements),
        "matrix_rank": matrix_rank,
        "condition_number": float(np.linalg.cond(system_matrix)),
        "generator_angles": sorted({item.generator_angle for item in measurements}),
        "analyzer_angles": sorted({item.analyzer_angle for item in measurements}),
        "used_files_in_order": [item.path.name for item in measurements],
        "center_pixel": {"x": center_x, "y": center_y},
        "center_mueller_raw": np.round(center_raw, 6).tolist(),
        "center_mueller_normalized": np.round(center_norm, 6).tolist(),
        "valid_normalized_pixel_count": int(valid_pixel_count),
        "average_normalized_mueller": np.round(normalized_mean, 6).tolist(),
    }

    write_summary_json(output_dir / "analysis_summary.json", summary)

    if not keep_measurement_stack and stack_path.exists():
        gc.collect()
        try:
            stack_path.unlink()
        except PermissionError:
            print(f"  Warning: could not delete temporary stack file: {stack_path}")

    print(f"  Output folder: {output_dir}")
    print("  Center pixel Mueller matrix (normalized):")
    print(np.round(center_norm, 6))
    print("  Average normalized Mueller matrix:")
    print(np.round(normalized_mean, 6))

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recover 3x3 image Mueller matrices from a folder of measurement images. "
            "Files should be named '<generator_angle>_<analyzer_angle>.<ext>'."
        )
    )
    parser.add_argument(
        "input_path",
        nargs="?",
        default=r"./20260515_4X4_song",
        help="A dataset folder or a parent folder containing multiple dataset folders.",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Root folder for analysis outputs. Default: '<input_path>_analysis'.",
    )
    parser.add_argument(
        "--dark-image",
        default=None,
        help="Optional dark image path that will be subtracted from every measurement image.",
    )
    parser.add_argument(
        "--chunk-rows",
        type=int,
        default=DEFAULT_CHUNK_ROWS,
        help="How many image rows to solve at one time. Smaller values use less RAM.",
    )
    parser.add_argument(
        "--skip-element-maps",
        action="store_true",
        help="Skip saving individual Mij element maps.",
    )
    parser.add_argument(
        "--skip-overview",
        action="store_true",
        help="Skip saving 3x3 subplot overview images.",
    )
    parser.add_argument(
        "--keep-measurement-stack",
        action="store_true",
        help="Keep the temporary stacked measurement file in each output folder.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_path = Path(args.input_path).resolve()
    output_root = (
        Path(args.output_root).resolve()
        if args.output_root
        else input_path.parent / f"{input_path.name}_analysis"
    )
    output_root.mkdir(parents=True, exist_ok=True)

    ext_priority = tuple(DEFAULT_EXTENSIONS)
    dark_image_path = Path(args.dark_image).resolve() if args.dark_image else None
    dataset_dirs = discover_dataset_dirs(input_path, ext_priority)

    batch_summary = []
    for dataset_dir in dataset_dirs:
        try:
            summary = analyze_dataset(
                dataset_dir=dataset_dir,
                output_root=output_root,
                ext_priority=ext_priority,
                dark_image_path=dark_image_path,
                chunk_rows=max(1, args.chunk_rows),
                save_individual_maps=not args.skip_element_maps,
                save_overview_images=not args.skip_overview,
                keep_measurement_stack=args.keep_measurement_stack,
            )
            batch_summary.append({"dataset": dataset_dir.name, "status": "ok", **summary})
        except Exception as error:
            batch_summary.append(
                {
                    "dataset": dataset_dir.name,
                    "status": "failed",
                    "error": str(error),
                }
            )
            print(f"  Failed to analyze {dataset_dir.name}: {error}")

    write_summary_json(output_root / "batch_summary.json", {"datasets": batch_summary})

    failed = [item for item in batch_summary if item["status"] != "ok"]
    print(f"\nFinished {len(batch_summary)} dataset(s).")
    print(f"Output root: {output_root}")
    if failed:
        print(f"Failed dataset count: {len(failed)}")
    else:
        print("All datasets finished successfully.")


if __name__ == "__main__":
    main()
