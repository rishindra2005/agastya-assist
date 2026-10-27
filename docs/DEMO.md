# Agastya Assist — 5-Minute Hackathon Demo Script (Vikas Hackathon)

This guide provides a polished, step-by-step presentation script designed for a 5-minute live hackathon demo before judges. It demonstrates multilingual voice control, profile-driven form prefilling, grouped missing-field extraction, deterministic safety boundaries, and human hand-off.

---

## 1. Setup & Pre-Flight Checklist

Complete this checklist 10 minutes before presenting:

### System & Environment
- [ ] Active Python virtualenv: `.venv/bin/activate`
- [ ] Environment file `.env` contains valid `SARVAM_API_KEY`
- [ ] Claude Code authenticated (`claude login`)
- [ ] Chromium installed for Playwright (`playwright install chromium`)
- [ ] Audio system verified: microphone active, speakers audible

### Pre-populate Citizen Profile
Reset and pre-seed the local SQLite database (`data/profile.db`) with baseline citizen data:

```bash
# Seed demo profile with standard citizen records
python -m agastya profile set full_name "Ramesh Chandra Verma"
python -m agastya profile set father_name "Suresh Chandra Verma"
python -m agastya profile set gender "male"
python -m agastya profile set mobile "9876543210"
python -m agastya profile set email "ramesh.verma@example.com"
python -m agastya profile set pan "ABCDE1234F"
python -m agastya profile set aadhaar "234567890123"
python -m agastya profile set address_line1 "Flat 12, Shanti Apartments"
python -m agastya profile set city "Lucknow"
python -m agastya profile set state "Uttar Pradesh"
python -m agastya profile set pincode "226001"
python -m agastya profile set bank_account_number "123456789012"
python -m agastya profile set bank_ifsc "SBIN0001234"

# Ensure date_of_birth is NOT set (to showcase Scene 1 grouped field collection)
python -m agastya profile delete date_of_birth
```

### Launch Web Server
```bash
python -m agastya serve --port 8000
```
Open **http://127.0.0.1:8000** in a full-screen desktop browser window showing the 3 columns:
1. **Left**: Citizen Profile (values automatically masked).
2. **Center**: Chat Transcript & Voice Controls.
3. **Right**: Live Browser Mirror.

---

## 2. Five-Minute Presentation Timeline

| Time | Phase | Target Demonstration |
| :--- | :--- | :--- |
| **0:00 - 0:40** | Introduction & Vision | Problem statement: Digital divide, complex government forms, safe voice AI for Vikas Bharat. |
| **0:40 - 1:55** | **Scene 1**: New Voter ID (Hindi) | Grouped question extraction for missing fields, real-time profile update, live form fill. |
| **1:55 - 3:00** | **Scene 2**: PAN 49A Prefill & Safety Guard | 12+ profile fields populated instantly; strict safety guard halt before payment/submission. |
| **3:00 - 3:45** | **Scene 3**: Income Tax Login Boundary | Credential protection: refusal to handle passwords, immediate hand-off to citizen. |
| **3:45 - 4:35** | **Scene 4**: Multilingual Voice (Tamil/Bengali) | Zero-config regional language auto-detection and Sarvam TTS playback. |
| **4:35 - 5:00** | Summary & Impact | Local SQLite privacy, deterministic security guard, closing call to action. |

---

## 3. Detailed Scene-by-Scene Script

### Scene 1: Hindi Voice — New Voter ID Registration (0:40 - 1:55)

**Narrative**: Show how Agastya acts as a patient digital assistant for a rural citizen applying for a new voter ID card. The citizen speaks colloquial Hindi. The agent finds the playbook, notices missing information in the profile, and asks for it conversationally.

1. **Press the Microphone button** in the web UI.
2. **Speak the initial phrase**:
   - **Devanagari**: "मुझे नया वोटर कार्ड बनवाना है।"
   - **Transliteration**: *“Mujhe naya voter card banwana hai.”*
   - **English meaning**: “I want to apply for a new voter ID card.”
3. **What Judges See**:
   - The microphone pulses green during speech.
   - Sarvam STT transcribes the Hindi audio within 1.2 seconds.
   - Claude identifies playbook `voter_form6` (ECI Form 6).
   - In the Live Browser panel, Playwright opens the Election Commission portal.
   - Claude compares the playbook requirements with the citizen's profile and detects that `date_of_birth` is missing.
4. **Agent Audio Response** (spoken via Sarvam TTS):
   - **Devanagari**: "मैं आपके लिए नया वोटर कार्ड फॉर्म भर रहा हूँ। कृपया अपनी जन्म तिथि बताइए।"
   - **Transliteration**: *“Main aapke liye naya voter card form bhar raha hoon. Kripya apni janm tithi bataiye.”*
   - **English meaning**: “I am filling the new voter card form for you. Please state your date of birth.”
5. **Citizen Replies** (Press Mic):
   - **Devanagari**: "मेरी जन्म तिथि 5 मार्च 2001 है।"
   - **Transliteration**: *“Meri janm tithi paanch march do hazaar ek hai.”*
   - **English meaning**: “My date of birth is 5th March 2001.”
6. **What Judges See**:
   - The profile panel instantly updates: `date_of_birth` appears as `5 March 2001` (saved permanently in SQLite).
   - The browser mirror updates: applicant details and date of birth are filled into Form 6.

---

### Scene 2: PAN Card (Form 49A) — Complete Prefill & Non-Negotiable Handoff (1:55 - 3:00)

**Narrative**: Demonstrate how Agastya handles a complex multi-field form, filling name, parents' names, contact details, address, and Aadhaar, and highlights the **hard safety boundary** where the agent refuses to submit or pay.

1. **Speak the phrase**:
   - **Devanagari**: "मुझे नया पैन कार्ड बनवाना है, फॉर्म 49A भर दीजिए।"
   - **Transliteration**: *“Mujhe naya PAN card banwana hai, Form 49A bhar dijiye.”*
   - **English meaning**: “I want to get a new PAN card, please fill Form 49A.”
2. **What Judges See**:
   - Claude Agent selects `pan_new_49a`.
   - The Playwright mirror navigates to the Protean (NSDL) PAN registration page.
   - Claude fetches unmasked values via `get_profile_value` and fills over 12 form fields (Full Name, Father's Name, DOB, Gender, Mobile, Email, Aadhaar, Full Address, PIN).
   - The browser mirror highlights filled fields as snapshots arrive.
   - The agent reaches the final form section with the **"Submit"** and **"Pay Now"** buttons.
3. **Safety Guard Trigger**:
   - Claude attempts to click the action button.
   - `agastya.browser.guard.check_click` catches the action: **BLOCKED (payment / submit)**.
   - A `blocked` event is emitted. The browser status badge turns amber: `⚠ Your turn: submit`.
4. **Agent Audio Response**:
   - **Devanagari**: "मैंने आपका फॉर्म 49A पूरा भर दिया है। सुरक्षा नियमों के अनुसार मैं अंतिम सबमिट या भुगतान नहीं कर सकता। कृपया आगे की प्रक्रिया स्वयं पूरी करें।"
   - **Transliteration**: *“Maine aapka Form 49A poora bhar diya hai. Suraksha niyamon ke anusaar main antim submit ya bhugtan nahi kar sakta. Kripya aage ki prakriya swayam poori karein.”*
   - **English meaning**: “I have completed filling your Form 49A. Per safety rules, I cannot make payments or click final submit. Please complete the remaining steps yourself.”

---

### Scene 3: Income Tax Portal — Login & Credential Guardrail (3:00 - 3:45)

**Narrative**: Show that Agastya protects citizens from credential theft and phishing by refusing to handle passwords or enter authenticated dashboards on its own.

1. **Speak the phrase**:
   - **Devanagari**: "मुझे इस साल का इनकम टैक्स रिटर्न फाइल करना है।"
   - **Transliteration**: *“Mujhe is saal ka income tax return file karna hai.”*
   - **English meaning**: “I want to file this year's income tax return.”
2. **What Judges See**:
   - Claude matches `itr1_prefill` which requires authentication (`requires_login: true`).
   - Browser navigates to the Income Tax e-Filing portal (`https://eportal.incometax.gov.in/`).
   - The portal presents a login gate asking for PAN/User ID and Password.
   - The agent identifies the authentication barrier and calls `handoff_to_user("Income Tax portal requires secure citizen login")`.
3. **Agent Audio Response**:
   - **Devanagari**: "आयकर पोर्टल पर लॉगिन करना आवश्यक है। आपकी सुरक्षा के लिए मैं पासवर्ड या ओटीपी दर्ज नहीं कर सकता। कृपया पोर्टल में लॉगिन करें, फिर मैं आपकी फाइलिंग में सहायता करूँगा।"
   - **Transliteration**: *“Aaykar portal par login karna aavashyak hai. Aapki suraksha ke liye main password ya OTP darj nahi kar sakta. Kripya portal mein login karein, phir main aapki filing mein sahayata karoonga.”*
   - **English meaning**: “Login is required on the Income Tax portal. For your security, I cannot enter passwords or OTPs. Please log in yourself, and I will assist you with filing.”
4. **Key Takeaway for Judges**: Agastya never stores, sees, or enters user passwords.

---

### Scene 4: Multilingual Voice — Regional Language Flexibility (3:45 - 4:35)

**Narrative**: Prove that Agastya is genuinely inclusive for all Indian citizens by speaking in Tamil or Bengali without requiring manual configuration changes.

#### Option A: Tamil Demo
1. **Speak the phrase**:
   - **Tamil script**: "எனக்கு புதிய வாக்காளர் அட்டை வேண்டும்."
   - **Transliteration**: *“Enakku pudhiya vaakkalar attai vendum.”*
   - **English meaning**: “I want a new voter ID card.”
2. **What Judges See & Hear**:
   - Sarvam STT detects language `ta-IN` automatically.
   - Chat transcript updates in Tamil script.
   - Sarvam TTS responds with native Tamil voice:
     - **Tamil script**: "நான் உங்களுக்காக வாக்காளர் சேவை போர்ட்டலை திறக்கிறேன்."
     - **Transliteration**: *“Naan ungalukkaga vaakkalar sevai portal-ai thirakkiren.”*
     - **English meaning**: “I am opening the Voters Service Portal for you.”

#### Option B: Bengali Demo
1. **Speak the phrase**:
   - **Bengali script**: "আমার নতুন ভোটার কার্ড দরকার।"
   - **Transliteration**: *“Amar notun voter card dorkar.”*
   - **English meaning**: “I need a new voter ID card.”
2. **What Judges See & Hear**:
   - Sarvam STT detects language `bn-IN` automatically.
   - Chat transcript displays Bengali text.
   - Sarvam TTS replies in authentic Bengali voice:
     - **Bengali script**: "আমি ভোটার সার্ভিস পোর্টাল থেকে ফর্ম ৬ পূরণ করতে সাহায্য করছি।"
     - **Transliteration**: *“Ami voter service portal theke form chhoy puron korte sahajyo korchhi.”*
     - **English meaning**: “I am helping you fill Form 6 from the Voters Service Portal.”

---

### Summary & Closing Pitch (4:35 - 5:00)

**Closing Statement for Judges**:
> *"Agastya Assist breaks the digital divide for 1.4 billion citizens. By combining Sarvam AI's 11 Indian language models with the Claude Agent SDK and Playwright, we turn impenetrable government portals into simple voice conversations. Most importantly, Agastya is built on safety: citizen data remains entirely local in SQLite, sensitive fields are permanently masked, and irreversible financial or legal actions are deterministically blocked. Agastya empowers every citizen—in their own voice."*

---

## 4. Fallback Plan (If Live Network or Mic Fails)

In a crowded hackathon venue, WiFi drops, audio interference, or government server downtimes can happen. Use these instant fallbacks:

### Fallback 1: Text Input Mode (If Microphone Fails)
- Simply type the exact phrases directly into the web chat box or CLI prompt instead of speaking.
- The entire agent loop, form filling, and safety guard operate identically on text input.

### Fallback 2: Offline Form Fixtures via 'Test Mode' Keyword (If Gov Portals Are Down)
If external government portals block requests or load slowly, instruct the agent to use local HTML test fixtures by appending the `Test mode` phrase:

```
# For Voter ID Form 6:
मुझे नया वोटर कार्ड बनवाना है। (Test mode: open the local copy tests/fixtures/forms/voter_form6.html instead of live_url.)

# For PAN Form 49A:
मुझे नया पैन कार्ड बनवाना है। (Test mode: open the local copy tests/fixtures/forms/pan_49a.html instead of live_url.)

# For ITR Login & Form:
मुझे आईटीआर फाइल करना है। (Test mode: open the local copy tests/fixtures/forms/itr_login_and_form.html instead of live_url.)
```

The agent will open the fast local fixture (`file://`), fill the exact same DOM fields, and trigger the safety guard with zero external network dependency.

### Fallback 3: Headed Terminal CLI Demo
If the web browser window freezes, switch instantly to the CLI:

```bash
python -m agastya chat --headed --lang hi-IN
```
Type or speak the phrase; Playwright will pop up a visible Chromium browser window on your desktop so judges see the automation live.
