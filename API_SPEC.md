# API 명세

호스트는 `http://127.0.0.1:8765` (충돌 시 서버가 다른 포트를 출력). 인증 없음. 로컬 전용.

변경 요청은 `requestId`로 중복을 식별한다. 같은 ID·같은 본문은 저장 결과를 반환하고, 같은 ID·다른 본문은 409 `request_conflict`다.

## 상태
- `status`: `created` | `running` | `waiting_for_user` | `done` | `error` | `cancelled` | `interrupted`
- `waiting_for_user`는 실패가 아니다.
- `waitKind`: `selection` | `question` | `editorial` | `storyboard` | `review` | null
- `stage`: `research` | `selection` | `investigate` | `editorial` | `storyboard` | `produce` | `review`

질문 객체: `id`, `version`, `question`, `options` (문자열 또는 `{id,label,hint}`), `allowFreeText`, `recommended`, `reason`.
답변은 `questionId`+`version`이 현재 질문과 같을 때만 반영한다. 오래된 답변은 409 `stale_question`.

## 엔드포인트

| 요청 | 본문 | 결과 |
|---|---|---|
| POST /api/projects | `{topic, periodDays?}` | `{projectId}` |
| GET /api/projects/{id} | | 후보·선택·상태·질문·산출물·오류 |
| POST /api/projects/{id}/research | `{requestId}` | `{runId, status}` |
| POST /api/projects/{id}/selection | `{candidateIds, version}` | 저장된 선택 |
| POST /api/projects/{id}/answers | `{questionId, version, answer, requestId}` | 수락 여부·상태 |
| POST /api/projects/{id}/approve | `{stage: storyboard\|final, version, requestId}` | 작업 ID 또는 최종 승인 |
| POST /api/projects/{id}/editorial | `{audience?, format?, hook?, version, requestId}` | 스토리보드 작업 ID (비운 항목은 AI 추천) |
| POST /api/projects/{id}/storyboard-changes | `{cardIds, cardNotes?, version, requestId}` | 스토리보드 재작성 작업 ID (승인 전에만) |
| POST /api/projects/{id}/revisions | `{cardIds, cardNotes?, regenerateImage?, instruction?, version, requestId}` | 수정 작업 ID. `cardNotes`는 카드별 고칠 글자 내용, `regenerateImage`는 이미지를 새로 만들 카드 ID. 메모가 없으면 Claude를 부르지 않고, `regenerateImage`에 없는 카드는 기존 이미지를 쓴다. 실패·취소 시 수정 전 카드와 기획으로 되돌아가 검수 화면을 유지한다 |
| POST /api/projects/{id}/back | `{requestId}` | 이전 대기 화면으로 (작업 중이면 409) |
| POST /api/runs/{id}/cancel | | 취소 상태. 작업 전 대기 화면으로 되돌림 |
| GET /api/projects/{id}/exports | | 최종 승인된 다운로드 목록 |
| GET /api/projects/{id}/files/{name} | | 검증된 산출물 파일 (카드 미리보기, `cards.zip`) |
| GET /api/projects/{id}/exports/{name} | | 최종 승인된 카드별 PNG(`card-01.png`…)와 `provenance.md`. 승인 전에는 404 |

오류 종류: `auth_required`, `usage_limit`(Claude 사용 한도 초과), `search_failed`, `image_failed`, `quota_exceeded`, `permission_denied`, `stale_question`, `invalid_answer`, `cancelled`, `request_conflict`.
각 오류는 `code`, `message`, `nextActions`를 포함한다.
