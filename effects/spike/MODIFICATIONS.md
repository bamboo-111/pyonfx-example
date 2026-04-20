# spike.py 参数说明

## 运行命令

```bash
cd d:/资源/GBC/特效

# 基本用法
python pyonfx-example/effects/spike/spike.py --input 皆无其名_input.ass --output 皆无其名_output.ass

# 带参数示例：加快逐字速度 + 提前完成溶解
python pyonfx-example/effects/spike/spike.py \
    --input 皆无其名_input.ass \
    --output 皆无其名_output.ass \
    --syllable-stagger-ms 20 \
    --dissolve-end-frac 0.85 \
    --pixel-fade-ms 100

# 速度优先（文件较大时可加快生成）
python pyonfx-example/effects/spike/spike.py \
    --input 皆无其名_input.ass \
    --output 皆无其名_output.ass \
    --quality-preset speed
```

---

## 参数说明与调节示例

### 字体出现

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `line_lead_in_ms` | 320 | 字在 karaoke 开始前多少 ms 出现 |
| `syllable_stagger_ms` | 40 | 逐字出现的间隔（ms），设为 0 则同时出现 |

```bash
# 加快逐字出现速度
--syllable-stagger-ms 20
# 所有字同时出现
--syllable-stagger-ms 0
```

---

### 字体溶解时序

溶解发生在每个音节自己的 karaoke 时间段内，从 karaoke 开始时启动，结束时完成。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `dissolve_start_frac` | 0.0 | 溶解开始的时间点，相对 karaoke 时长的比例（0.0 = karaoke 开始） |
| `dissolve_end_frac` | 1.0 | 溶解完成的时间点，相对 karaoke 时长的比例（1.0 = karaoke 结束） |
| `pixel_fade_ms` | 180 | 每段色块从出现到消失的淡出时长（ms） |
| `mask_steps` | 32 | 字体分割为多少段进行溶解，越多越细腻 |

```bash
# 字体在 karaoke 前 80% 时间内完成溶解（视觉上消失得更早、更干脆）
--dissolve-end-frac 0.8

# 字体从 karaoke 10% 处开始溶解，到 90% 处完成
--dissolve-start-frac 0.1 --dissolve-end-frac 0.9

# 缩短每段色块的淡出时间，溶解看起来更硬朗
--pixel-fade-ms 80
```

---

### 冒刺

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `spike_total_count` | 32 | 每个音节的冒刺总数 |
| `spike_min_count` | 12 | 最少冒刺数（时间很短时的保底） |
| `spike_speed_min` / `spike_speed_max` | 36 / 175 | 刺的飞行速度范围 |
| `spike_travel_distance` | 44.0 | 刺的飞行距离 |
| `spike_lifetime_min_ms` / `spike_lifetime_max_ms` | 40 / 600 | 刺的存在时长范围 |

```bash
# 减少冒刺数量，加快飞行速度
--spike-total-count 16 --spike-speed-min 80 --spike-speed-max 250

# 让刺飞得更远
--spike-travel-distance 80
```

---

### 品质与性能

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `quality_preset` | quality | 品质预设：`quality` / `balanced` / `speed` |

```bash
# 速度优先（生成更快，效果略简化）
--quality-preset speed
```
