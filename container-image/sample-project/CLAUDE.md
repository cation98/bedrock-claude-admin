# Sample Project

FastAPI 기반 안전관리 데이터 조회 샘플 프로젝트입니다.

## 프로젝트 구조
```
sample-project/
├── app/
│   ├── main.py          # FastAPI 앱
│   ├── config.py        # 설정
│   ├── models.py        # SQLAlchemy 모델
│   ├── schemas.py       # Pydantic 스키마
│   └── routes/
│       └── safety.py    # 안전관리 API
└── requirements.txt
```

## 실행 방법
```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```
