# CCA Power Meter Dashboard - Setup Guide

## Step 1: Create Tuya IoT Cloud Project

1. Go to [https://iot.tuya.com](https://iot.tuya.com)
2. Sign up / Log in (you can use the same email: controllab@iitmandi.ac.in)
3. Go to **Cloud** > **Development** > **Create Cloud Project**
4. Fill in:
   - Project Name: `CCA Power Meter`
   - Industry: `Smart Energy`
   - Development Method: `Smart Home`
   - Data Center: **India** (since IIT Mandi)
5. Click **Create**

## Step 2: Get API Credentials

1. In your cloud project, go to **Overview**
2. Copy the **Access ID/Client ID**
3. Copy the **Access Secret/Client Secret**

## Step 3: Subscribe to Required APIs

In your cloud project, go to **API Explorer** or **Service API** tab:
1. Subscribe to **IoT Core** (for device management)
2. Subscribe to **Smart Home Device Management** 
3. Subscribe to **Device Status Notification** (optional, for real-time updates)

## Step 4: Link Your Tuya Smart App Account

1. In the cloud project, go to **Devices** tab
2. Click **Link Tuya App Account**
3. Choose **Add App Account** and scan the QR code with the Tuya Smart app
   - Open Tuya Smart app (logged in as controllab@iitmandi.ac.in)
   - Go to **Me** > **Settings** (gear icon top-right)
   - Tap the **QR code scanner** icon
   - Scan the QR code on the website
4. Your devices will now appear in the cloud project

## Step 5: Configure the Dashboard

1. Copy the env file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and fill in your credentials:
   ```
   TUYA_ACCESS_ID=your_actual_access_id
   TUYA_ACCESS_SECRET=your_actual_access_secret
   TUYA_API_ENDPOINT=https://openapi.tuyain.com
   ```

## Step 6: Install & Run

```bash
# Install Python dependencies
pip install -r requirements.txt

# Run the server
python server.py
```

Open your browser to: **http://localhost:5000**

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Token error" | Check Access ID and Secret are correct |
| No devices showing | Make sure you linked the Tuya app account (Step 4) |
| "permission deny" | Subscribe to required APIs (Step 3) |
| Wrong data center | Change `TUYA_API_ENDPOINT` in `.env` to match your region |

## API Endpoints (for India)

| Region | Endpoint |
|--------|----------|
| India | `https://openapi.tuyain.com` |
| China | `https://openapi.tuyacn.com` |
| US West | `https://openapi.tuyaus.com` |
| Europe | `https://openapi.tuyaeu.com` |
