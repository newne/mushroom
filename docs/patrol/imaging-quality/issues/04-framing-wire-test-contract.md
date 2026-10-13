# 04 `test_framing_wire` 的用例喂数组，与生产契约（字节）相反

- 提出：**step code**（外部协作者）　执行：**巡检手**　状态：ready-for-agent
- 立项：2026-10-13

## 事实

`FramingHook.evaluate` 的契约是 `Callable[[bytes], FramingError]`
（`patrol/orchestrator.py:39`），生产链路也确实给字节：`_shoot` → `framing.pixels`
→ MinIO 原文（`orchestrator.py:91-98`）。

但 `deploy/tests/test_framing_wire.py:70` 的 `test_evaluate_is_the_bright_region_offset`
直接喂**灰度 ndarray**：
```python
err = got.hook.evaluate(gray)      # gray 是 np.ndarray
```
这个用例绿，是因为它绕过了生产那条路。它没能发现的问题：字节喂进
`bright_region_offset` 会抛 `ValueError: could not convert string to float`
（2026-10-13 复现）——`--framing` 一开就会在第一个站位把整轮带崩。因为默认
`--framing off`，线上从未触发。

## 修复现状（已落地，等这条票收口）

`deploy/framing_wire.py` 已补上漏掉的解码层，并留了一个**兼容分支**保住上面那个用例：
```python
return imread(pixels) if isinstance(pixels, (bytes, bytearray)) else pixels
```

## 交付

1. 把该用例改成**生产口径**：喂 JPEG 字节（用一张小的真实 JPEG，或 `imread` 的逆操作），
   断言 `evaluate` 返回 `found=True` 且偏移方向对；
2. 补一条反向用例：喂字节而不解码时必须失败（钉住"解码不能漏"）；
3. 两条都绿之后，删掉 `framing_wire.to_gray` 里的 `isinstance` 兼容分支。

## 验收

- `patrol-workspace` 全量 pytest 绿；
- `grep -n "isinstance(pixels" deploy/src/deploy/framing_wire.py` 无结果。
