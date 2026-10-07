from __future__ import annotations

import hashlib

from engineering.config import ROOT

SKILLS = {'data-retrieval': '动态取数、指标口径与查询验证',
          'olist-engineering': 'Olist 增量接入、三层建模与质量门'}


def load(name):
    if name not in SKILLS:
        raise ValueError('未注册的 skill')
    content = (ROOT / 'skills' / name / 'SKILL.md').read_text(encoding='utf-8')
    return {'name': name, 'sha256': hashlib.sha256(content.encode()).hexdigest(), 'instructions': content}
