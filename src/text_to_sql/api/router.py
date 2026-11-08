"""POST /api/text-to-sql. 서비스는 Depends로 주입한다.
오류 매핑: 422(입력), 502(LLM 불가), 500(검증 차단·내부)."""
