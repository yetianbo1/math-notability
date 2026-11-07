"""Native tape pattern enum, checked against the 2026-10-29 sample and local generated schema.
CreateInk: tool slot 4 = 3 (Tape), style slot 5 = 1 (FixedWidth),
optional tapePattern slot 6. Evidence is kept outside the release in validation.
"""
PATTERNS = ((0, '斜纹'), (1, '网格'), (2, '圆点'), (3, '纯色'),
            (4, '星星'), (5, '花朵'), (6, '爱心'), (7, '波浪'), (8, '棋盘格'))
