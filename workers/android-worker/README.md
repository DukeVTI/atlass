# Atlas Android Worker

A headless background daemon that gives Atlas (VPS Brain) full access to Duke's Android phone.

## Architecture

```
Atlas VPS Brain
     │
     │ WebSocket (WSS)
     ▼
Atlas API Hub (/ws endpoint)
     │
     │ Pushes task JSON
     ▼
Atlas Android Worker (this app)
     │
     ├── speak()          → phone speaker (TTS)
     ├── get_location()   → GPS + reverse geocode
     ├── read_sms()       → SMS inbox
     ├── send_sms()       → programmatic SMS send
     ├── push_notification() → local push notification
     ├── read_notifications() → all app notifications
     ├── get_device_stats()  → battery, memory, device info
     └── read_contacts()  → phone contacts search
```

## Setup

### 1. Install dependencies
```bash
npm install
```

### 2. Configure environment
```bash
cp .env.template .env
# Edit .env with your VPS IP and worker token
```

### 3. Install as bare workflow (required for SMS native modules)
```bash
npx expo prebuild --platform android
```

### 4. Build and install APK
```bash
# Development build (run on connected phone)
npx expo run:android

# Production APK via EAS
npm install -g eas-cli
eas login
eas build --platform android --profile preview
```

## VPS Setup

Copy `MOBILE_TOOL_FOR_VPS.py` to `services/orchestrator/tools/mobile.py` on the VPS.

Then in `services/orchestrator/main.py`, add:
```python
from tools.mobile import MobileTool
registry.register(MobileTool())
```

Then rebuild:
```bash
docker-compose up -d --build orchestrator
```

## Android Permissions Required

After installing, grant these in Android Settings:
- **Location** → Allow all the time (for background GPS)
- **SMS** → Allow
- **Contacts** → Allow
- **Notifications** → Allow
- **Special access → Notification access** → Enable Atlas Worker (for reading other apps' notifications)
- **Special access → Battery optimization** → Unrestricted (so Android doesn't kill the daemon)

## Tool Protocol

The VPS sends:
```json
{ "task_id": "uuid", "tool": "speak", "kwargs": { "text": "Hello Duke" } }
```

The phone responds:
```json
{ "task_id": "uuid", "status": "success", "result": "Spoke: Hello Duke" }
```

## File Structure

```
atlas-android/
  App.tsx                          ← minimal status UI + daemon init
  hooks/useAtlasDaemon.ts          ← WebSocket + tool dispatch engine
  tools/
    index.ts                       ← tool registry
    tts.ts                         ← speak / stop_speaking
    location.ts                    ← get_location
    sms.ts                         ← read_sms / send_sms
    notifications.ts               ← push_notification / read_notifications
    device.ts                      ← get_device_stats / read_contacts
  services/
    ForegroundService.ts           ← keeps daemon alive on Android
  MOBILE_TOOL_FOR_VPS.py          ← copy to orchestrator/tools/mobile.py on VPS
  app.json                         ← Expo config + Android permissions
  package.json                     ← dependencies
  .env.template                    ← environment variables template
```
