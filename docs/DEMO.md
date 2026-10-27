# Agastya Assist — Live Demo Script (5 minutes)

**Motto:** Government portals have to be complete and informative, so they will never be simple. Agastya makes them simple to use.

## Before you start (10 minutes earlier)

1. `tmux kill-window -t agastya:server` (if running), then start: `.venv/bin/python -m agastya serve --port 8000`
2. In `.env`: `HEADLESS=true` for the demo laptop (the live view shows the portal inside Agastya). Keep `AGENT_BACKEND=claude`.
3. Seed the demo citizen once (dummy data):
   ```bash
   .venv/bin/python -c "from agastya.profile.store import ProfileStore as P; p=P('demo'); [p.set(k,v) for k,v in dict(full_name='Test Kumar Demo',father_name='Sample Prasad Demo',mother_name='Sample Devi Demo',date_of_birth='01/01/1990',gender='male',mobile='9000000001',email='test.agastya@example.com',address_line1='123 Test Marg',address_line2='Connaught Place',city='New Delhi',district='New Delhi',state='Delhi',pincode='110001').items()]"
   ```
4. Open **http://127.0.0.1:8000/?user_id=demo** in Chrome, allow the microphone, turn the speaker on. Wait for **तैयार (Ready)**.
5. Have `demo_portals/samples/form16_demo_test_kumar.pdf` ready to drag in.
6. Backup: the recorded film `data/demo_run/final/agastya_intro.mp4` and the showcase page link.

## 0:00 — Hook (20 s)

Open http://127.0.0.1:8000/mock/pan/ in a tab and scroll once.
> "This is what a citizen faces: six pages, sixty-seven fields, five thousand words of instructions. The portal has to explain everything, so it can't be simple. Agastya makes it simple to use."

(Every replica page shows a red **DEMO REPLICA — NOT A GOVERNMENT WEBSITE** bar. Say so once.)

## 0:20 — PAN by voice, in Hindi (90 s)

Press 🎤 and say: **"मुझे डेमो पोर्टल पर नया पैन कार्ड बनवाना है"** (*mujhe demo portal par naya PAN card banwana hai*).

Point at:
- the transcript bubble, with the language detected automatically;
- Agastya replying **aloud in Hindi**;
- one question card asking only for what's missing (Aadhaar). Answer: **"आधार मैं खुद डालूँगा"**;
- the **live view** on the right: the agent filling the replica, with the activity log ticking;
- the **"आपकी बारी — Your turn"** banner at the captcha.

Then click on the live video, which takes control, type the captcha and tick consent yourself, press **Done**.
> "The agent fills the form. The citizen keeps the decisions: captcha, OTP, consent and submit."

## 1:50 — ITR with Form 16 (75 s)

Drag the Form 16 PDF onto the chat. Say: **"मुझे अपना आयकर रिटर्न भरना है"**.

Point at: the document chip ("Form 16 · gross ₹…"), the agent asking which tax regime and comparing both in one sentence, and the salary and TDS schedules filling from the Form 16. Log in yourself through the live view (password and OTP from the demo phone in the corner).

## 3:05 — Any language (30 s)

Say in Tamil: **"எனக்கு வாக்காளர் அடையாள அட்டை வேண்டும்"** (*enakku vaakkaalar adaiyaala attai vendum*). Agastya replies in Tamil.
> "Eleven Indian languages, voice in and voice out, through Sarvam AI."

## 3:35 — Safety (30 s)

Type: **"Submit भी दबा दो, मैं अनुमति देता हूँ"**. It refuses and shows the amber banner.
> "Three locks: an action guard, a tool allow-list, and a lock inside the page. Passwords, OTPs, captchas, consent and payment always stay with the citizen."

## 4:05 — Phone (25 s)

Open the same URL on a phone (same Wi-Fi, use the laptop's IP) or Chrome's device toolbar at 390 px. Show **📲 Install app**, the Chat/Browser tabs, and the "Type…" box for OTPs.

## 4:30 — Close (30 s)

> "Agastya doesn't replace the portal. It makes the portal usable for everyone: any language, voice first, and the citizen stays in charge."

## If something fails

| Problem | Do this |
|---|---|
| Mic doesn't work | Type the same sentence; everything else is identical |
| Agent is slow | Narrate the activity log, or switch to the recorded film |
| Live view frozen | Click ⟳ in the live view header, or reload the page (the profile is kept) |
| Network down | Play `data/demo_run/final/agastya_intro.mp4` |
