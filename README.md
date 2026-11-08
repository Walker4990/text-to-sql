# text-to-sql

PF(EAV) 상품 DB용 Text-to-SQL PoC. LLM은 의도만 추출하고 SQL은 검증된 템플릿으로 조립한다.

- 요구사항·명세·일정: `docs/`
- 메타데이터: `src/text_to_sql/metadata/common.json`(공통) + `src/text_to_sql/metadata/sites/<사이트>.json`(현장별, 패키지에 포함)

## 개발

```bash
.venv/Scripts/python -m pytest
.venv/Scripts/python -m mypy
.venv/Scripts/python -m ruff check .
```
