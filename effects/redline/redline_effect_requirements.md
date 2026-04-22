# Redline 需求规格 v2（基于 `template.py`）

> **变更说明**：本版在 v1 基础上做了以下类别的修订——消除歧义与矛盾、补充缺失约束、精简冗余描述、统一术语、强化可验证性。每处实质修改以 `[修订]` 标注原因。

---

## 1. 文档目的

本文件定义从 `redline/template.py` 空模板出发，实现完整字幕特效所需的全部需求。开发者按此文档即可直接编码，不依赖额外口头说明。

---

## 2. 范围

### 2.1 In Scope

1. 基于每个 `syl` 在其下方生成红色手绘装饰长线（ASS `\p` 绘图）。
2. 长线具备三层扰动：静态手绘误差、连续慢速 flow、逐帧 boil 重绘。
3. 长线在 `syl` 活动期内从**右向左**恒速几何消失（遮罩裁切）。`[修订：v1 在§4.4与§10.4中分别写了"从左到右"消失和"左端始终保持端帽"，两者矛盾——若从左到右消失，左端最先消失，不可能始终保持端帽。统一为从右向左消失：右端逐步裁切，左端始终保留圆帽，与火花簇位置一致。]`
4. 在主线左端维护 3–4 道常驻短线作为火花簇，随左端位置同步移动。`[修订：v1 说"左侧"但消失方向说"左到右"，逻辑上火花应在存留端。现统一：火花在左端，消失从右端推进，火花始终可见直到 syl 结束。]`
5. `syl.end_time` 后长线整体淡出，同时在左端位置生成手绘简笔蝴蝶并飞走。
6. 蝴蝶包含翅膀扇动循环（少帧抽帧风格）和末段渐隐。
7. 保持 `template.py` 现有 CLI、并行渲染、输出流程完全兼容。

### 2.2 Out of Scope

1. 实时 shader、外部依赖库、ASS 解析框架改动、复杂粒子物理仿真。

---

## 3. 术语定义

`[修订：v1 缺少统一术语表，导致"主线""长线""红线"混用，"火花短线""左侧短线""burn slash"指代不清。]`

| 术语 | 含义 |
|---|---|
| **主线（main stroke）** | 每个 syl 下方的红色手绘装饰长线 |
| **火花簇（spark cluster）** | 主线左端附近的 3–4 道常驻短线 |
| **单道火花（spark slash）** | 火花簇中的一条短线 |
| **蝴蝶（butterfly）** | syl 结束后飞出的手绘简笔蝴蝶 |
| **切片（slice）** | 一个动画帧对应的时间区间 |
| **遮罩前沿（mask front）** | 主线几何消失的推进边界 x 坐标 |

---

## 4. 输入输出

### 4.1 输入

1. ASS 文件（默认路径 `in.ass`）。
2. 目标行由 `_select_target_lines()` 筛选（默认：非 comment 且底对齐 alignment ∈ {1,2,3}）。
3. 每个 `line` 的 `syls`（PyonFX 解析提供）。
4. `MeltConfig` 配置参数。

### 4.2 输出

1. ASS 文件（默认路径 `output.ass`）。
2. 每个 `syl` 产出以下事件组（相对 `line_layer_base` 的层偏移）：

| 层偏移 | 内容 | 时段 |
|---|---|---|
| +0 | 主线主层 | `[t0, t1]` |
| +1 | 主线柔化层 | `[t0, t1]` |
| +2 | 火花簇 | `[t0, t1]` |
| +3 | 蝴蝶身体 | `[t1, t1+Tb]` |
| +4 | 蝴蝶翅膀 | `[t1, t1+Tb]` |

`[修订：v1 §12 写 LAYERS_PER_LINE=3 但列了 5 层，矛盾。此处明确为 5 层，代码中 LAYERS_PER_LINE 需同步改为 5。]`

---

## 5. 视觉目标

1. 主线呈偏暗红手绘线条质感，不是程序化直线。
2. 运动整体"轻、慢、克制"，不抢字幕主体视觉重心。
3. 逐帧 boil 有"每帧重描"的手绘感，不是简单丢帧。
4. 消失机制为从右向左几何裁切（遮罩前沿推进），不是整条同时变透明。
5. 主线左端始终保持半圆手绘端帽（round cap）。
6. 火花簇表现为散开 + 轻微抖动 + 缓慢跟随，不做燃烧消亡。
7. 蝴蝶为少帧手绘抽帧循环扇翅 + 弧线飞行 + 末段渐隐。

---

## 6. 时间语义

以单个 `syl` 为单位：

```
t0 = syl.start_time              # 主线开始
t1 = syl.end_time                # 主线结束 / 蝴蝶触发
Dt = max(1, t1 - t0)             # 主线活动时长（防零除）
Tb = butterfly_duration_ms       # 蝴蝶飞行总时长
Tf = butterfly_fade_ms           # 蝴蝶末段渐隐时长（Tf ≤ Tb）
```

| 阶段 | 时段 | 内容 |
|---|---|---|
| 主线活动期 | `[t0, t1)` | 主线显示 + 遮罩消失 + 火花簇 |
| 蝴蝶期 | `[t1, t1+Tb)` | 蝴蝶飞行 + 扇翅 + 渐隐 |

`[修订：v1 未明确 Tf ≤ Tb 约束，若 Tf > Tb 会导致淡出起点在蝴蝶出现之前。]`

---

## 7. 配置参数（`MeltConfig` 扩展）

在现有字段外新增以下字段。分组仅为可读性，实际为同一 dataclass 的 flat 字段。

### 7.1 采样与时间

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `anim_fps` | int | 10 | 动画帧率 |
| `min_slice_ms` | int | 40 | 单切片最短时长（防过密） |

`[修订：移除 v1 中 line_mask_grain_px 和 line_mask_soft_px——它们描述"逐粒子消失"的遮罩纹理，但§10.4 实际算法是基于 x_cut 的几何裁切 + 边缘随机抖动，不需要独立的 grain/soft 参数。若需要控制边缘锯齿程度，由 mask_edge_jitter_px 替代（见§7.8）。]`

### 7.2 主线几何

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `line_extend_px` | float | 12.0 | 主线向 syl 两侧延伸量 |
| `line_width_px` | float | 7.0 | 主线带状宽度 |
| `line_width_jitter_ratio` | float | 0.06 | 宽度随机波动比例 |
| `curve_arc_px` | float | 1.8 | 中心线中点弧度偏移 |
| `underline_offset_px` | float | 4.0 | 主线相对 syl.bottom 的下方偏移 `[修订：v1 §10.1 提到此参数但标注"若无单独参数可先固定"，现给出明确默认值。]` |
| `sample_density_px` | float | 10.0 | 每多少 px 一个采样点 |
| `sample_min_points` | int | 18 | 采样点数下限 |
| `sample_max_points` | int | 64 | 采样点数上限 |

### 7.3 扰动

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `static_amp_px` | float | 1.1 | 静态误差振幅 |
| `static_cycles` | float | 2.4 | 静态误差沿线周期数 |
| `flow_amp_px` | float | 1.6 | flow 扰动振幅 |
| `flow_cycles` | float | 2.0 | flow 沿线周期数 |
| `flow_speed_hz` | float | 0.22 | flow 时间频率 |
| `boil_amp_px` | float | 0.5 | boil 逐帧振幅 |
| `boil_cycles` | float | 3.0 | boil 沿线周期数 |

### 7.4 火花簇

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `spark_count_min` | int | 3 | 同时可见火花最少数 `[修订：统一 burn_slash → spark 前缀]` |
| `spark_count_max` | int | 4 | 同时可见火花最多数 |
| `spark_len_min_px` | float | 8.0 | 单道火花最短长度 |
| `spark_len_max_px` | float | 20.0 | 单道火花最长长度 |
| `spark_follow_smooth` | float | 0.7 | 跟随平滑因子（0=硬跟随，1=不跟随） |
| `spark_jitter_amp_px` | float | 1.2 | 逐帧位置抖动幅度 |
| `spark_drift_px` | float | 6.0 | 向左缓慢漂移总量 |
| `spark_spread_deg_min` | float | 145.0 | 扇开角度下限（度） |
| `spark_spread_deg_max` | float | 225.0 | 扇开角度上限（度） |

### 7.5 蝴蝶

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `butterfly_duration_ms` | int | 560 | 飞行总时长 |
| `butterfly_fade_ms` | int | 180 | 末段渐隐时长（≤ duration） |
| `wing_cycle_fps` | int | 8 | 翅膀帧率 |
| `butterfly_dx_px` | float | 44.0 | 飞行水平位移（正=向右） |
| `butterfly_dy_px` | float | -36.0 | 飞行垂直位移（负=向上） |
| `butterfly_arc_px` | float | 18.0 | 飞行弧线峰值偏移 |
| `butterfly_scale` | float | 1.0 | 整体缩放 |

### 7.6 样式颜色

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `line_color_main` | str | `"&H2D2DCC&"` | 主线主层颜色 |
| `line_color_soft` | str | `"&H3A3AE0&"` | 主线柔化层颜色 |
| `spark_color` | str | `"&H4A4AFF&"` | 火花颜色 |
| `butterfly_color` | str | `"&H5A5AFF&"` | 蝴蝶颜色 |
| `line_alpha_main` | str | `"&H10&"` | 主线主层透明度 |
| `line_alpha_soft` | str | `"&H40&"` | 主线柔化层透明度 |
| `spark_alpha` | str | `"&H30&"` | 火花透明度 |
| `butterfly_alpha` | str | `"&H08&"` | 蝴蝶基础透明度 |
| `soft_blur` | float | 0.9 | 柔化层模糊值 |

### 7.7 遮罩消失 `[修订：新增分组，将散落在算法描述中的遮罩参数集中管理]`

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `mask_edge_jitter_px` | float | 2.0 | 遮罩前沿边缘随机锯齿幅度，避免机械直切 |

---

## 8. 数据结构

### 8.1 保留结构

`OutputEvent`、`LayerShape` 保持模板定义不变。

### 8.2 新增内部结构

所有使用 `@dataclass(frozen=True, slots=True)`。

| 结构 | 字段 | 用途 |
|---|---|---|
| `CenterPoint` | `x: float, y: float, s: float` | 中心线采样点，`s` 为归一化弧长 ∈ [0,1] |
| `RibbonPath` | `left: list[tuple[float,float]], right: list[tuple[float,float]]` | 带状展开后的左右边界点序列 |
| `SparkSlash` | `seed: int, anchor_x: float, anchor_y: float, angle_deg: float, length: float` | 单道火花的静态参数 |
| `ButterflyFrame` | `frame_index: int, drawing: str` | 蝴蝶单帧绘图 |
| `SylContext` | `line, syl, line_layer_base: int, style_name: str, seed: int, config: MeltConfig` | 减少函数间重复传参的上下文包 |

`[修订：移除 v1 中的 CenterlineFrame（仅在生成管线内部使用，不需要持久化结构）和 SparkSlash.local_phase（火花是常驻的，phase 由 seed 决定，无需独立字段）。]`

### 8.3 可复现性

所有随机数生成基于 `random.Random(config.random_seed + line.i * 1000 + syl.i)` 实例，禁止使用全局 random 状态。`[修订：v1 只说"基于 seed 可复现"但未禁止全局状态，多进程下全局状态不可复现。]`

---

## 9. 模块职责

### 9.1 `_select_target_lines(lines, selector=None) -> list[tuple[int, Line]]`

**新增函数**。所有目标行筛选逻辑集中于此，`render_spike` 不得散落条件判断。默认 selector：`lambda line: not line.comment and line.styleref.alignment in (1, 2, 3)`。

### 9.2 `text_to_layer_shapes(syl, config) -> list[LayerShape]`

返回主线的材质定义列表（至少主层 + 柔化层共 2 项）。`drawing` 字段此阶段为空字符串，由后续时间切片动态填充。`[修订：v1 函数签名只有 obj，缺少 config 参数，无法读取颜色配置。]`

### 9.3 `_build_full_shape_events(...) -> list[OutputEvent]`

生成主线基础层事件。覆盖时段 `[t0, t1]`。职责边界：只负责无遮罩裁切的完整主线渲染（用于与遮罩层叠加，或作为遮罩层的底层）。

`[修订：v1 说"不负责 t1 后线条淡出"但未说明谁负责。明确：t1 后的淡出由 _build_vector_mask_events 在最后一个切片中附加一个短淡出事件完成。]`

### 9.4 `_build_vector_mask_events(...) -> list[OutputEvent]`

**核心函数**。按切片逐帧输出：

1. 主线动态路径（flow + boil 扰动后的带状几何）。
2. 从右向左遮罩裁切（基于 `x_cut` 截断几何，左端保留圆帽）。
3. 火花簇事件（3–4 道常驻短线，锚定于主线左端，每帧轻微抖动 + 漂移）。
4. 最后一帧附加主线淡出过渡（可选，时长 ≤ `line_fade_in_ms`）。

### 9.5 `_build_spike_events(...) -> list[OutputEvent]`

覆盖时段 `[t1, t1+Tb]`。输出蝴蝶身体 + 翅膀循环帧 + 弧线飞行位移 + 末段渐隐。

### 9.6 `melt_line(...)`

对每个非空 `syl` 串联 §9.2–§9.5，控制层级偏移与时序。

---

## 10. 算法规范

### 10.1 基础中心线

```
x_left  = syl.left  - config.line_extend_px
x_right = syl.right + config.line_extend_px
y_base  = syl.bottom + config.underline_offset_px

n = clamp(
    round((x_right - x_left) / config.sample_density_px),
    config.sample_min_points,
    config.sample_max_points
)
```

对 `i ∈ [0, n-1]`：`s = i / (n-1)`，`x = lerp(x_left, x_right, s)`，`y = y_base - curve_arc_px × 4s(1-s)`。

### 10.2 法线扰动

对每个采样点 `P(x, y)`：

1. 切线 `T = normalize(P[i+1] - P[i-1])`（端点用单侧差分）。
2. 法线 `N = (-T.y, T.x)`。
3. 位移 `d = d_static(s) + d_flow(s, t) + d_boil(s, k)`：
   - `d_static(s) = static_amp × sin(2π × static_cycles × s + phase_static)`
   - `d_flow(s, t) = flow_amp × sin(2π × (flow_cycles × s + flow_speed_hz × t_sec) + phase_flow)`
   - `d_boil(s, k) = boil_amp × hash_noise(s, k, seed)`，其中 `k` 为帧序号（整数），`hash_noise` 返回 `[-1, 1]`
4. `P' = P + N × d`

`[修订：v1 未给出 d_static / d_flow / d_boil 的具体函数形式，仅说"叠加"。现给出确定性公式，消除实现歧义。phase_static / phase_flow 由 seed 决定。]`

### 10.3 带状展开

```
w_i = 0.5 × line_width_px × (1 + line_width_jitter_ratio × hash_noise(s_i, 0, seed+1))
L_i = P'_i + N_i × w_i
R_i = P'_i - N_i × w_i

path = L_0 → L_1 → ... → L_{n-1} → R_{n-1} → R_{n-2} → ... → R_0 → close
```

### 10.4 从右向左遮罩消失

```
u = clamp((t - t0) / Dt, 0, 1)
x_cut = x_right - (x_right - x_left) × u + jitter(seed, t)
```

其中 `jitter` 幅度 ≤ `mask_edge_jitter_px`。

裁切规则：丢弃所有 `x > x_cut` 的采样点。对跨越 `x_cut` 的线段做线性插值截断。截断端（右端）无需端帽。左端始终附加半圆端帽（半径 ≈ `line_width_px / 2`），用 4–6 点近似半圆弧。

`[修订：方向统一为从右向左。v1 此处是最大矛盾点。]`

### 10.5 火花簇

1. 初始化时用 seed 生成 `spark_count`（∈ [min, max]）道火花，每道确定 `angle_deg`（∈ [spread_min, spread_max]）和 `length`（∈ [len_min, len_max]）。
2. 每帧锚点 `(ax, ay)` 跟随主线左端点，跟随公式：`ax_new = lerp(ax_prev, left_tip_x, 1 - spark_follow_smooth)`。
3. 每帧叠加 `spark_jitter_amp_px` 范围的随机偏移和 `spark_drift_px × u` 的累计左漂。
4. 每道火花绘制为 2 点手绘短线段（可选 3 点折线增加手绘感）。

### 10.6 蝴蝶

**飞行路径**（`u = clamp((t - t1) / Tb, 0, 1)`）：

```
x = x0 + butterfly_dx_px × u
y = y0 + butterfly_dy_px × u - butterfly_arc_px × 4u(1-u)
```

**翅膀帧**：3 帧循环（closed → mid → open），帧索引 `k = floor((t - t1) × wing_cycle_fps / 1000) % 3`。

**渐隐**：当 `t > t1 + Tb - Tf` 时，alpha 从基础值线性插值到 `&HFF&`（全透明）。

---

## 11. 蝴蝶矢量形状

由身体 + 左翅 + 右翅组成（右翅为左翅水平镜像）。

3 帧定义：

| 帧名 | 翅膀状态 |
|---|---|
| `closed` | 翅膀近乎合拢，面积最小 |
| `mid` | 翅膀半展 |
| `open` | 翅膀全展，面积最大 |

身体为固定细梭形，所有帧共用。单色填充，无描边。缩放系数由 `butterfly_scale` 控制。具体 `\p` 绘图坐标由实现者定义，但必须保证三帧风格一致、仅翅膀张角不同。

---

## 12. 性能约束

1. 每 syl 切片数 = `ceil(Dt / (1000 / anim_fps))`，硬上限 200 切片。
2. 每 syl 总事件数建议 ≤ 90 条（含火花与蝴蝶）；若超出，优先降低 `anim_fps`。
3. 禁止 O(n²) 全局几何布尔运算。
4. 兼容模板现有多进程策略（spawn context，worker 函数无副作用）。

---

## 13. 错误处理与降级

| 异常条件 | 降级策略 |
|---|---|
| `syl` 宽度 < 2px | 跳过该 syl，不生成任何事件 |
| `Dt` < `min_slice_ms` | 生成单切片静态主线（无 flow/boil），保留端帽 |
| 带状路径构建异常 | 跳过该切片，继续后续切片 |
| 蝴蝶帧构建异常 | 回退为单帧静态蝴蝶 + 位移 + 淡出 |
| `butterfly_fade_ms > butterfly_duration_ms` | 自动截断为 `butterfly_duration_ms` |

---

## 14. 代码级要求

1. 所有实现保持在 `redline/template.py` 单文件内（首版）。
2. `LAYERS_PER_LINE` 更新为 5。
3. 新增数据结构使用 `@dataclass(frozen=True, slots=True)`。
4. 绘图坐标统一使用整数（`round()` 后输出），避免浮点精度造成 ASS 渲染差异。`[修订：v1 说"整数或 0.1 精度统一策略"，未做选择。现选定整数。]`
5. 所有新增函数添加单行 docstring。

---

## 15. 测试

### 15.1 单元测试（必须）

1. 固定 seed 下 `x_cut(t)` 严格单调递减（从右向左）。
2. 法线向量长度 ∈ `[0.99, 1.01]`。
3. `RibbonPath` 左右边界点数相等且 ≥ 2。
4. 翅膀帧索引 `k` 循环范围 = `{0, 1, 2}`。
5. `butterfly_fade_ms > butterfly_duration_ms` 时自动截断。

### 15.2 视觉回归（建议）

用固定 seed + 样例 ASS 验证：

1. 同 seed 两次运行输出二进制一致。
2. 消失方向为从右向左。
3. 左端持续可见圆帽 + 火花簇。
4. 每个 syl 末尾出现蝴蝶并飞走。

---

## 16. 里程碑

| 阶段 | 交付物 |
|---|---|
| A | 主线几何 + 扰动 + 带状路径 → 静态 ASS 输出 |
| B | 从右向左遮罩消失 + 左端圆帽 |
| C | 火花簇（3–4 常驻 + 跟随 + 抖动） |
| D | 蝴蝶帧资产 + 飞行 + 扇翅 + 渐隐 |
| E | 调参、性能验收、边界用例测试 |

---

## 17. 验收清单

全部通过方可视为完成：

1. 从 `template.py` 执行 `python -m redline.template --input in.ass` 生成目标 ASS 且无异常。
2. 主线具备可感知的手绘质感（静态误差 + boil）与慢速 flow 运动。
3. 每个 syl 内主线从右向左恒速几何消失，非整条变透明。
4. 消失过程中左端始终保持半圆端帽。
5. 消失过程中左端附近始终可见 3–4 道火花短线，随左端移动。
6. 每个 syl 结束时触发蝴蝶，具备 3 帧抽帧扇翅、弧线飞行、末段渐隐。
7. 固定 seed 下两次运行输出一致。
8. 单 syl 事件数 ≤ 90。

