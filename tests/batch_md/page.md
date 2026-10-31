---
title: "机器学习在肝细胞癌早期筛查中的应用"
source: "E:/Dowmload/anymd/tests/samples/page.html"
type: "html"
engine: "markitdown"
converted_at: "2026-10-19T11:02:34+08:00"
---

# 机器学习在肝细胞癌早期筛查中的应用

## 研究背景

肝细胞癌（HCC）起病隐匿，多数患者确诊时已处于中晚期。早期筛查对改善预后至关重要。

## 主要方法

* 血清标志物：AFP、GALAD 评分
* 影像组学：CT、MRI 特征提取
* 机器学习模型：XGBoost、随机森林、深度学习

| 模型 | 样本量 | AUC | 敏感度 |
| --- | --- | --- | --- |
| GALAD | 1200 | 0.89 | 82% |
| XGBoost | 860 | 0.92 | 86% |
| CNN | 540 | 0.94 | 88% |

## 结论

多模态融合模型在早期筛查中表现最佳，但外部验证仍然不足。

```
print("hello anymd")
```
