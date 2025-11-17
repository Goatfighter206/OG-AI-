# 🔥 Windows Firewall Setup for Mobile Access

## Allow Your Phone to Connect

To access OG-AI from your phone, you need to allow Python through Windows Firewall.

---

## ⚡ Quick Fix (Easiest)

### Temporarily Disable Firewall (Testing Only):
1. Press **Windows + I** (Settings)
2. Go to **Update & Security** → **Windows Security**
3. Click **Firewall & network protection**
4. Click your active network (Private/Public)
5. Turn **OFF** Microsoft Defender Firewall
6. **Test from phone:** http://10.0.0.96:8000
7. **Turn firewall back ON** when done testing

**Note:** Only for testing! Turn it back on after.

---

## 🛡️ Proper Fix (Recommended)

### Allow Python Through Firewall:

#### Method 1: Automatic (Run this command)
```powershell
# Run PowerShell as Administrator
netsh advfirewall firewall add rule name="OG-AI Agent" dir=in action=allow protocol=TCP localport=8000
```

#### Method 2: Manual (GUI)

1. **Open Firewall Settings:**
   - Press **Windows + R**
   - Type: `wf.msc`
   - Press **Enter**

2. **Create Inbound Rule:**
   - Click **Inbound Rules** (left sidebar)
   - Click **New Rule...** (right sidebar)
   - Select **Port** → Click **Next**
   - Select **TCP**
   - Type **8000** in Specific local ports
   - Click **Next**

3. **Allow the Connection:**
   - Select **Allow the connection**
   - Click **Next**

4. **Apply to Networks:**
   - Check **Private** (your home WiFi)
   - Check **Domain** (if applicable)
   - Uncheck **Public** (for security)
   - Click **Next**

5. **Name the Rule:**
   - Name: `OG-AI Agent`
   - Description: `Allow OG-AI on port 8000`
   - Click **Finish**

6. **Test from Phone:**
   - Open: http://10.0.0.96:8000
   - Should work now!

---

## 🔍 Verify Firewall Rule

### Check if Rule Exists:
```powershell
# Run in PowerShell
netsh advfirewall firewall show rule name="OG-AI Agent"
```

Should show: `Rule Name: OG-AI Agent`

---

## ✅ Test Connection

### From Computer:
```bash
curl http://localhost:8000/health
```
Should return: `{"status":"healthy"...}`

### From Phone:
1. Make sure phone is on **same WiFi**
2. Open browser
3. Go to: `http://10.0.0.96:8000`
4. Should see OG-AI epic interface!

---

## 🚨 Still Not Working?

### Check These:

**1. Is Server Running?**
```bash
curl http://localhost:8000/health
```
If this fails, server isn't running. Start it:
```bash
python app.py
```

**2. Correct IP Address?**
Your IP might have changed. Check:
```bash
ipconfig | findstr "IPv4"
```
Use the IP shown (e.g., `10.0.0.96`)

**3. Same WiFi?**
- Check computer WiFi name
- Check phone WiFi name
- Must match exactly

**4. Antivirus Blocking?**
- Some antivirus software blocks connections
- Temporarily disable to test
- Add exception for Python if needed

**5. Router Issues?**
- Some routers block device-to-device communication
- Check router settings for "AP Isolation" or "Client Isolation"
- Should be **disabled**

---

## 🔐 Security Notes

### Current Setup:
- **Port 8000** open on your local network
- **Not exposed to internet** (safe)
- **Only accessible** from devices on your WiFi
- **No authentication** required (local use only)

### For Public Access:
- Would need port forwarding (not recommended)
- Better to deploy to cloud (Render, Heroku, etc.)
- See deployment guides

---

## 📱 Quick Mobile Test

Once firewall is configured:

1. **On computer, open:** http://localhost:8000/qr
2. **Scan QR code with phone**
3. **Or manually visit:** http://10.0.0.96:8000
4. **Should see OG-AI!** 🎉

---

## 🔧 Remove Firewall Rule (If Needed)

### To Delete the Rule:
```powershell
# Run PowerShell as Administrator
netsh advfirewall firewall delete rule name="OG-AI Agent"
```

Or manually:
1. Open `wf.msc`
2. Find **OG-AI Agent** in Inbound Rules
3. Right-click → **Delete**

---

## 💡 Pro Tips

1. **Keep firewall ON** - Use rule instead of disabling
2. **Private network only** - Don't allow on Public networks
3. **Test with QR code** - Visit http://localhost:8000/qr
4. **Add to home screen** - Quick access on phone
5. **Close port 8000** - When not using OG-AI (delete rule)

---

## 📊 Network Diagram

```
Your Setup:
┌─────────────┐
│   Router    │ ← Your WiFi
└──────┬──────┘
       │
   ┌───┴───┬─────────┐
   │       │         │
┌──┴──┐ ┌──┴──┐  ┌──┴──┐
│ PC  │ │Phone│  │Other│
│8000 │ │     │  │     │
└─────┘ └─────┘  └─────┘
   ↑       │
   └───────┘
  Connection!
```

---

## ✅ You're All Set!

After configuring firewall:
- ✅ Phone can access OG-AI
- ✅ Same WiFi required
- ✅ Full voice features work
- ✅ All epic visuals load
- ✅ Secure (local network only)

**Visit on your phone:** http://10.0.0.96:8000 🔥📱
