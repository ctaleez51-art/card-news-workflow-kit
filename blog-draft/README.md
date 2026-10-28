# 학습 기록 → 블로그 초안

교안-6 확장 과제. `daily` 학습 기록을 선택해 주제 후보와 근거를 받고, 사람이 주제와 길이를 고르면 Markdown 초안을 만든다.

PowerShell에서 다음 명령을 순서대로 실행한다.

```powershell
cd 'C:\Users\ctale\Desktop\claudecode\card-news-workflow-kit\blog-draft'
```

```powershell
python -m pip install -r 'C:\Users\ctale\Desktop\claudecode\card-news-workflow-kit\blog-draft\requirements.txt'
```

```powershell
python -m app
```

브라우저에서 `http://127.0.0.1:8766`을 연다. Claude CLI 로그인이 필요하다. 기록과 발행 목록은 한 단계 위의 `claudecode/daily`에서 읽는다. 결과는 `blog-draft/outputs`에 저장한다.
