import os
import numpy as np
from PIL import Image


def linear_stokes(angle_deg):
    theta = np.deg2rad(angle_deg)
    return np.array([
        1.0,
        np.cos(2 * theta),
        np.sin(2 * theta)
    ], dtype=np.float64)


def read_image_as_float(path):
    img = Image.open(path)
    img = np.array(img)

    if img.ndim == 3:
        img = img[..., :3]
        img = np.mean(img, axis=2)

    return img.astype(np.float64)


def build_system_matrix(generator_angles, analyzer_angles):
    """
    I = A @ M @ S
    若 M 用 row-major 展開，則係數為 kron(A, S)

    順序固定為:
    A0_G0, A0_G45, A0_G90,
    A45_G0, A45_G45, A45_G90,
    A90_G0, A90_G45, A90_G90
    """
    H = []
    for a_deg in analyzer_angles:
        A = linear_stokes(a_deg)
        for g_deg in generator_angles:
            S = linear_stokes(g_deg)
            H.append(np.kron(A, S))
    return np.array(H, dtype=np.float64)


def find_existing_file(input_dir, base_name, ext_priority):
    for ext in ext_priority:
        path = os.path.join(input_dir, base_name + ext)
        if os.path.exists(path):
            return path
    return None


def load_9_images_by_naming(input_dir, angles=(0, 45, 90),
                            ext_priority=(".tiff", ".png", ".bmp")):
    """
    你的命名方式:
    0_45 = G0_A45

    但反解需要的順序是:
    A0_G0, A0_G45, A0_G90,
    A45_G0, A45_G45, A45_G90,
    A90_G0, A90_G45, A90_G90

    所以讀檔時轉成 base_name = f"{g}_{a}"
    """
    generator_angles = list(angles)
    analyzer_angles = list(angles)

    image_list = []
    used_files = []
    shape_ref = None

    for a in analyzer_angles:
        for g in generator_angles:
            base_name = f"{g}_{a}"   # 因為你的命名是 G_A
            file_path = find_existing_file(input_dir, base_name, ext_priority)

            if file_path is None:
                raise FileNotFoundError(f"找不到檔案: {base_name}，支援副檔名 {ext_priority}")

            img = read_image_as_float(file_path)

            if shape_ref is None:
                shape_ref = img.shape
            elif img.shape != shape_ref:
                raise ValueError(f"影像尺寸不一致: {file_path}, shape={img.shape}, expected={shape_ref}")

            image_list.append(img)
            used_files.append(file_path)

    image_stack = np.stack(image_list, axis=0)  # (9, H, W)
    return image_stack, used_files


def solve_single_pixel_mueller(pixel_intensities, generator_angles, analyzer_angles):
    """
    pixel_intensities: shape = (9,)
    對單一 pixel 的 9 個量測值反解 3x3 Mueller matrix
    """
    Hmat = build_system_matrix(generator_angles, analyzer_angles)   # (9, 9)
    Hinv = np.linalg.pinv(Hmat)                                     # (9, 9)

    m_vec = Hinv @ pixel_intensities                                # (9,)
    M = m_vec.reshape(3, 3)
    return M


def normalize_single_mueller_by_m00(M, eps=1e-12):
    return M / (M[0, 0] + eps)


if __name__ == "__main__":
    # =========================
    # 1. 路徑與參數設定
    # =========================
    input_dir = r"./20260326量測/p30"
    angles = (0, 45, 90)
    ext_priority = (".tiff", ".png", ".bmp")

    generator_angles = [0, 45, 90]
    analyzer_angles = [0, 45, 90]

    # 若有 dark image 可填路徑，否則設 None
    dark_image_path = None

    # =========================
    # 2. 讀取 9 張影像
    # =========================
    image_stack, used_files = load_9_images_by_naming(
        input_dir=input_dir,
        angles=angles,
        ext_priority=ext_priority
    )

    print("實際使用的檔案：")
    for f in used_files:
        print(f)

    n_meas, H, W = image_stack.shape
    print(f"\nimage_stack shape = {image_stack.shape}")
    print(f"Image size = {W} x {H}")

    # =========================
    # 3. 背景扣除
    # =========================
    if dark_image_path is not None:
        dark = read_image_as_float(dark_image_path)
        if dark.shape != (H, W):
            raise ValueError(f"dark image 尺寸不符: {dark.shape} vs {(H, W)}")
        image_stack = image_stack - dark[None, :, :]
        image_stack = np.clip(image_stack, 0, None)

    # =========================
    # 4. 讓使用者輸入 pixel 座標
    # =========================
    x = int(input(f"\n請輸入 x 座標 (0 ~ {W-1}): "))
    y = int(input(f"請輸入 y 座標 (0 ~ {H-1}): "))

    if not (0 <= x < W and 0 <= y < H):
        raise ValueError(f"輸入座標超出範圍: x={x}, y={y}")

    # =========================
    # 5. 取出該 pixel 的 9 個強度值
    # =========================
    pixel_intensities = image_stack[:, y, x]   # shape = (9,)

    print("\n此 pixel 的 9 個 intensity values：")
    print(np.round(pixel_intensities, 4))

    # =========================
    # 6. 解單一 pixel 的 3x3 Mueller matrix
    # =========================
    M_pixel = solve_single_pixel_mueller(
        pixel_intensities=pixel_intensities,
        generator_angles=generator_angles,
        analyzer_angles=analyzer_angles
    )

    M_pixel_norm = normalize_single_mueller_by_m00(M_pixel)

    # =========================
    # 7. 顯示結果
    # =========================
    print(f"\nPixel coordinate: (x={x}, y={y})")

    print("\nSingle-pixel Mueller matrix (raw):")
    print(np.round(M_pixel, 6))

    print("\nSingle-pixel Mueller matrix (normalized by m00):")
    print(np.round(M_pixel_norm, 6))