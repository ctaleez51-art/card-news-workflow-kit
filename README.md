# 카드뉴스 워크플로

주제 하나를 입력하면 최신 자료를 조사하고, 브라우저에서 고른 뒤 카드뉴스를 만드는 로컬 웹 앱입니다. 설계는 `PRD.md`, 결정은 `DECISIONS.md`, 검수는 `ACCEPTANCE.md`를 봅니다.

## 실행

Claude CLI 로그인과 웹 검색 권한, Antigravity CLI(`agy`) 이미지 생성이 이 계정에서 실제로 되는지를 먼저 확인하세요. 설치만 되어 있고 로그인이 없으면 앱이 성공한 척하지 않고 오류를 보여 줍니다.

```bash
pip install -r requirements.txt
python -m app
```

브라우저에서 안내된 주소를 엽니다. 기본은 `http://127.0.0.1:8765`이며, 포트가 쓰이면 다음 포트를 출력합니다. 서버는 `127.0.0.1`에만 바인딩합니다.

1. `AI 최신소식`을 입력하고 조사를 시작합니다.
2. 후보 1~3개를 고른 뒤에만 제작이 이어집니다.
3. 질문이 뜨면 답하고, 스토리보드를 승인한 뒤 이미지를 만듭니다.
4. 글자는 Pillow로 이미지와 따로 붙입니다. 카드 크기는 1080×1350입니다.
5. 최종 승인 후 PNG ZIP과 `provenance.md`를 받습니다.

결과물은 `outputs/{projectId}/`에 저장됩니다.

이미지 권한 시험이 필요할 때만, 해당 실행에 한해 `CARDNEWS_AGY_SKIP_PERMISSIONS=1`을 쓸 수 있습니다. 이 옵션은 이미지 도구만이 아니라 그 `agy` 실행의 도구 요청을 자동 승인합니다.

### Gemini API 키로 agy 실행 (Windows PowerShell)

계정의 이미지 한도가 소진됐을 때 씁니다. 공식 [설치·인증 문서](https://www.antigravity.google/docs/install?tab=cli) 기준입니다.

1. `C:\Users\{사용자}\.gemini\antigravity-cli\settings.json`에 `"modelProvider": "gemini"`를 넣습니다. 이 설정 없이 키만 넣으면 효과가 없습니다.
2. 키를 사용자 환경 변수로 등록합니다. `GOOGLE_API_KEY`나 `.env`는 읽지 않습니다.

```powershell
[System.Environment]::SetEnvironmentVariable('GEMINI_API_KEY', '여기에_키', 'User')
```

3. 새 PowerShell 창에서 서버를 켭니다.

```powershell
cd 'C:\Users\ctale\Desktop\claudecode\card-news-workflow-kit'
```

```powershell
$env:CARDNEWS_AGY_SKIP_PERMISSIONS = '1'
```

```powershell
python -m app
```

되돌리려면 `settings.json`에서 `modelProvider`를 지우고 agy를 다시 켭니다.

실행 검증 결과와 실패 사례는 `VALIDATION.md`에 있습니다.

## 교안-6 확장

학습 기록에서 블로그 초안을 만드는 별도 앱은 [blog-draft/README.md](blog-draft/README.md)를 봅니다. 정상 입력과 자료 부족 입력의 실제 실행 결과는 [blog-draft/VALIDATION.md](blog-draft/VALIDATION.md)에 기록했습니다.

실제 카드 미리보기와 초안 예시는 [evidence/README.md](evidence/README.md)에 있습니다.
