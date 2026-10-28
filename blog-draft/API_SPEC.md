# API

| 요청 | 입력 | 결과 |
|---|---|---|
| `GET /api/files` | 없음 | 선택 가능한 daily 파일명 |
| `POST /api/candidates` | `filename` | 프로젝트 ID, 상태(`select` 또는 `insufficient`), 후보와 근거 절 또는 부족한 점 |
| `POST /api/drafts` | `projectId`, `candidateId`, `length`(`short`/`medium`/`long`) | 초안 파일명, Markdown, 문단별 근거 검사 |
| `GET /api/projects/{id}` | ID | 저장된 상태 |
| `GET /api/projects/{id}/files/{name}` | ID, `draft-vN.md` | Markdown 파일 |

잘못된 파일명·선택은 400, 자료 부족 상태에서 초안 요청은 409, Claude 실패는 502로 응답한다.
