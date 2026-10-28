RESULT_FILE = "run_result.json"

JSON_RULES = f"""
작업이 끝나면 작업 폴더의 {RESULT_FILE}에 UTF-8 JSON만 Write 도구로 저장하세요.
파일 저장에 성공했으면 대화에는 JSON을 다시 쓰지 말고 한 줄로 끝내세요. Write가 실패했을 때만 대화 마지막에 JSON 전체를 출력하세요.
확인하지 않은 날짜·수치·출시 여부를 단정하지 마세요. 모르면 unknown으로 두세요.
기사 제목만으로 사실을 확정하지 마세요. 가능하면 발표 기관·공식 문서·원문을 WebFetch로 확인하세요.
공개 예정과 실제 출시를 구분하세요. 업체 주장과 독립 확인 사실을 구분하세요.
과거 예시를 최신 검색 결과인 것처럼 쓰지 마세요.
"""


def research_prompt(topic: str, period_days: int, as_of: str) -> str:
    return f"""당신은 카드뉴스용 뉴스 조사 에이전트입니다.
주제: {topic}
조사 기준 시각(한국 시간): {as_of}
조사 기간: 최근 {period_days}일.

WebSearch와 WebFetch로 실제 웹을 검색하세요. 가능하면 서로 다른 소식 7~12개를 찾으세요.
같은 발표를 다룬 기사는 묶고 clusterId를 같게 하세요.
모델·제품·연구·개발 도구 등 다양성을 확보하세요.
검색 도구를 쓸 수 없거나 실패하면 status=error, code=search_failed 또는 auth_required.

후보가 4개 미만이면 찾은 것만 넣고 status=need_input 으로 기간을 30일로 넓힐지 물으세요.

{JSON_RULES}

스키마:
{{
  "status": "result" | "need_input" | "error",
  "code": null | "search_failed" | "auth_required",
  "message": "짧은 설명",
  "researchAsOf": "{as_of}",
  "periodDays": {period_days},
  "candidates": [
    {{
      "id": "c1",
      "clusterId": "cluster-a",
      "title": "",
      "summary": "",
      "publishedAt": "YYYY-MM-DD 또는 unknown",
      "eventAt": "YYYY-MM-DD 또는 unknown",
      "url": "원문 URL",
      "whySelect": "",
      "uncertainty": ""
    }}
  ],
  "question": null | {{
    "id": "expand_period",
    "question": "결과가 적습니다. 조사 기간을 30일로 넓힐까요?",
    "options": ["30일로 넓히기", "현재 결과로 진행"],
    "allowFreeText": false,
    "recommended": "30일로 넓히기",
    "reason": "최근 {period_days}일에서 확인된 후보가 적습니다."
  }}
}}
"""


def investigate_prompt(topic: str, selected: list[dict], period_days: int, as_of: str) -> str:
    import json

    return f"""선택한 후보만 심층 조사하세요. 주제: {topic}
조사 기준: {as_of}, 기간 {period_days}일.
선택 후보:
{json.dumps(selected, ensure_ascii=False, indent=2)}

각 후보의 원문·공식 문서를 WebFetch로 확인하세요.
독자·카드 형식·후킹 문구 2~3개를 제안하세요.
status=result 로 추천과 이유를 적으세요. 최종 선택은 사용자가 화면에서 합니다. question은 null로 두세요.
후킹 문구는 확인된 사실 범위 안에서만.

{JSON_RULES}

스키마:
{{
  "status": "result" | "need_input" | "error",
  "code": null,
  "message": "",
  "investigation": {{
    "facts": [{{"text": "", "source": "URL"}}],
    "claims": [{{"text": "", "who": "", "source": "URL"}}],
    "unknowns": [""],
    "conflicts": [""]
  }},
  "editorial": {{
    "audience": "일반 사용자|개발자|교육자 중 하나만 그대로",
    "audienceReason": "",
    "format": "비교|단계설명|스토리|체크리스트 중 하나만 그대로",
    "hooks": ["", ""]
  }},
  "question": null | {{
    "id": "audience",
    "question": "어떤 독자에게 전달할까요?",
    "options": [
      {{"id": "general", "label": "일반 사용자", "hint": "의미와 사용 조건 중심"}},
      {{"id": "developer", "label": "개발자", "hint": "도구·API·변경점 중심"}},
      {{"id": "educator", "label": "교육자", "hint": "설명·수업 활용 중심"}}
    ],
    "allowFreeText": true,
    "recommended": "developer",
    "reason": ""
  }}
}}
"""


def storyboard_prompt(audience: str | None, extra: str | None) -> str:
    extra_line = extra or "추가 지시 없음"
    audience_line = audience or "조사 결과의 추천 독자"
    return f"""심층 조사와 편집 방향을 바탕으로 카드뉴스 스토리보드를 만드세요.
독자: {audience_line}
추가: {extra_line}
보통 5~8장. 표지는 관심, 본문은 장마다 핵심 하나, 마지막은 요약 또는 다음 행동.
근거 없는 순위·수치·과장 금지.
각 카드에 역할, 제목, 핵심 문장, 짧은 본문, 근거 URL, 그림 계획(글자 없는 장면)을 적으세요.

{JSON_RULES}

스키마:
{{
  "status": "result" | "error",
  "storyboard": {{
    "title": "",
    "cards": [
      {{
        "id": "s1",
        "role": "cover|body|close",
        "title": "",
        "headline": "",
        "body": "",
        "source": "URL 또는 조사 기록",
        "imagePlan": "글자 없는 장면 설명, 여백, 색감"
      }}
    ]
  }}
}}
"""


def storyboard_change_prompt(instruction: str) -> str:
    return f"""방금 만든 스토리보드를 사용자의 변경 요청대로 다시 만드세요.
변경 요청: {instruction}

지정한 카드만 고치고, 나머지 카드와 독자·형식·후킹 문구는 그대로 두세요.
보통 5~8장. 근거 없는 순위·수치·과장 금지. 확인된 사실 범위 안에서만 쓰세요.

{JSON_RULES}

스키마:
{{
  "status": "result" | "error",
  "editorial": null | {{
    "audience": "",
    "audienceReason": "",
    "format": "",
    "hooks": [""]
  }},
  "storyboard": {{
    "title": "",
    "cards": [
      {{
        "id": "s1",
        "role": "cover|body|close",
        "title": "",
        "headline": "",
        "body": "",
        "source": "URL 또는 조사 기록",
        "imagePlan": "글자 없는 장면 설명, 여백, 색감"
      }}
    ]
  }}
}}
"""


def revision_prompt(card_ids: list[str], instruction: str) -> str:
    import json

    return f"""승인된 스토리보드에서 지정한 카드만 수정하세요. 나머지 카드는 그대로 두세요.
대상 카드 ID: {json.dumps(card_ids, ensure_ascii=False)}
수정 요청: {instruction}

전체 스토리보드를 다시 {RESULT_FILE}에 저장하세요. 수정하지 않은 카드는 내용을 유지하세요.
{JSON_RULES}
스키마는 스토리보드와 같습니다.
"""
