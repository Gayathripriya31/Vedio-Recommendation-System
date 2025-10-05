# Video Recommendation API

## Setup
1. Create venv and activate
```powershell
py -3 -m venv venv
.\venv\\Scripts\\Activate.ps1
```
2. Env vars in `.env`
```env
FLIC_TOKEN=...
API_BASE_URL=https://api.socialverseapp.com
```
3. Install deps
```powershell
.\venv\\Scripts\\python.exe -m pip install -r requirements.txt
```

## Run
```powershell
.\venv\\Scripts\\python.exe -m uvicorn app.main:app --reload
```

## Endpoints
- GET `/health`
- POST `/videos`, GET `/videos`, GET `/videos/{id}`, DELETE `/videos/{id}`
- POST `/users`, GET `/users/{id}`
- POST `/interactions`
- GET `/recommendations/{user_id}`

## Postman
Import `postman/VideoRecommendationAPI.postman_collection.json` into Postman.
