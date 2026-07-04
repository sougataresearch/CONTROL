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

    return img.astype(np.float32)


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


def average_normalized_mueller_over_all_pixels(
    image_stack,
    generator_angles,
    analyzer_angles,
    chunk_rows=200,
    eps=1e-12,
    use_mask=False,
    m00_threshold=1.0
):
    """
    先對每個 pixel 解 3x3 Mueller matrix，再用各自 m00 做 normalization，
    最後平均所有 pixel 的 normalized MM。

    這裡用 chunk 逐塊處理，避免一次吃太多記憶體。

    Parameters
    ----------
    image_stack : ndarray, shape = (9, H, W)
    chunk_rows : int
        每次處理多少列
    eps : float
        避免除以 0
    use_mask : bool
        是否只平均 m00 大於 threshold 的 pixel
    m00_threshold : float
        若 use_mask=True，只有 m00 > threshold 的 pixel 會納入平均

    Returns
    -------
    M_avg_norm : ndarray, shape = (3, 3)
        所有 pixel 的 normalized MM 平均值
    valid_pixel_count : int
        實際納入平均的 pixel 數
    """
    n_meas, H, W = image_stack.shape

    Hmat = build_system_matrix(generator_angles, analyzer_angles)   # (9, 9)
    Hinv = np.linalg.pinv(Hmat)                                     # (9, 9)

    sum_norm = np.zeros((9,), dtype=np.float64)
    valid_pixel_count = 0

    for y0 in range(0, H, chunk_rows):
        y1 = min(y0 + chunk_rows, H)

        # 取出一個 chunk: (9, chunkH, W)
        chunk = image_stack[:, y0:y1, :]

        # 攤平為 (9, N)
        b = chunk.reshape(n_meas, -1).astype(np.float64)

        # 解每個 pixel 的 raw MM 向量: (9, N)
        m_vec = Hinv @ b

        # m00 是 row-major 下的第一個元素
        m00 = m_vec[0, :]

        if use_mask:
            valid_mask = np.abs(m00) > m00_threshold
        else:
            valid_mask = np.abs(m00) > eps

        if np.any(valid_mask):
            m_norm = m_vec[:, valid_mask] / (m00[valid_mask][None, :] + eps)
            sum_norm += np.sum(m_norm, axis=1)
            valid_pixel_count += m_norm.shape[1]

    if valid_pixel_count == 0:
        raise ValueError("沒有有效 pixel 可供平均，請檢查 m00_threshold 或資料內容。")

    mean_norm_vec = sum_norm / valid_pixel_count
    M_avg_norm = mean_norm_vec.reshape(3, 3)

    return M_avg_norm, valid_pixel_count


if __name__ == "__main__":
    # =========================
    # 1. 路徑與參數設定
    # =========================
    input_dir = r"./20260326量測/air"
    angles = (0, 45, 90)
    ext_priority = (".tiff", ".png", ".bmp")

    generator_angles = [0, 45, 90]
    analyzer_angles = [0, 45, 90]

    # 若有 dark image 可填路徑，否則設 None
    dark_image_path = None

    # chunk 大小，可依電腦記憶體調整
    chunk_rows = 200

    # 是否只平均亮區 pixel
    use_mask = False
    m00_threshold = 1.0

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
    # 4. 計算所有 pixel 的 normalized MM 再平均
    # =========================
    M_avg_norm, valid_pixel_count = average_normalized_mueller_over_all_pixels(
        image_stack=image_stack,
        generator_angles=generator_angles,
        analyzer_angles=analyzer_angles,
        chunk_rows=chunk_rows,
        eps=1e-12,
        use_mask=use_mask,
        m00_threshold=m00_threshold
    )

    # =========================
    # 5. 顯示結果
    # =========================
    print(f"\n有效納入平均的 pixel 數量: {valid_pixel_count}")

    print("\nAverage of per-pixel normalized 3x3 Mueller matrix:")
    print(np.round(M_avg_norm, 6))