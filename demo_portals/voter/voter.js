/**
 * Voters' Service Portal (Demo Replica) client logic.
 * Fictional demo helpers for state/district/AC dependent dropdowns,
 * transliteration box, preview generation, login OTP, and captcha handling.
 */

// 1. Bridge for automation testing when ?demo_test=1 is present
(function initTestCaptchaBridge() {
  if (new URLSearchParams(window.location.search).get('demo_test') === '1') {
    const origFillText = CanvasRenderingContext2D.prototype.fillText;
    let charBuffer = '';
    CanvasRenderingContext2D.prototype.fillText = function (text, x, y) {
      if (this.canvas && this.canvas.closest && this.canvas.closest('.demo-captcha')) {
        charBuffer += text;
        if (charBuffer.length === 5) {
          window.__demoCaptchaAnswer = charBuffer;
          charBuffer = '';
        }
      }
      return origFillText.apply(this, arguments);
    };
  }
})();

// 2. Dependent dropdown dataset for Section A (4 states with plausible constituencies)
const VOTER_LOCATION_DATA = {
  "MH": {
    name: "Maharashtra",
    districts: {
      "mumbai_suburban": {
        name: "Mumbai Suburban",
        constituencies: [
          "154 - Bandra West",
          "165 - Chandivali",
          "152 - Andheri East"
        ]
      },
      "pune": {
        name: "Pune",
        constituencies: [
          "214 - Pune Cantonment",
          "215 - Kasba Peth",
          "208 - Vadgaon Sheri"
        ]
      },
      "nagpur": {
        name: "Nagpur",
        constituencies: [
          "56 - Nagpur West",
          "57 - Nagpur South"
        ]
      },
      "thane": {
        name: "Thane",
        constituencies: [
          "146 - Ovala Majiwada",
          "147 - Kopri-Pachpakhadi"
        ]
      }
    }
  },
  "KA": {
    name: "Karnataka",
    districts: {
      "bengaluru_urban": {
        name: "Bengaluru Urban",
        constituencies: [
          "150 - Bellandur",
          "160 - Sarvagnanagar",
          "162 - Shivajinagar"
        ]
      },
      "mysuru": {
        name: "Mysuru",
        constituencies: [
          "216 - Krishnaraja",
          "217 - Chamaraja"
        ]
      },
      "belagavi": {
        name: "Belagavi",
        constituencies: [
          "11 - Belagavi Rural",
          "12 - Belagavi Uttar"
        ]
      }
    }
  },
  "DL": {
    name: "NCT of Delhi",
    districts: {
      "central_delhi": {
        name: "Central Delhi",
        constituencies: [
          "21 - Matia Mahal",
          "22 - Ballimaran"
        ]
      },
      "south_delhi": {
        name: "South Delhi",
        constituencies: [
          "43 - Malviya Nagar",
          "44 - Kalkaji"
        ]
      },
      "new_delhi": {
        name: "New Delhi",
        constituencies: [
          "40 - New Delhi",
          "41 - Jangpura"
        ]
      }
    }
  },
  "UP": {
    name: "Uttar Pradesh",
    districts: {
      "lucknow": {
        name: "Lucknow",
        constituencies: [
          "171 - Lucknow Central",
          "172 - Lucknow Cantonment"
        ]
      },
      "varanasi": {
        name: "Varanasi",
        constituencies: [
          "388 - Varanasi North",
          "389 - Varanasi South"
        ]
      },
      "kanpur_nagar": {
        name: "Kanpur Nagar",
        constituencies: [
          "213 - Sishamau",
          "214 - Arya Nagar"
        ]
      }
    }
  }
};

window.VOTER_LOCATION_DATA = VOTER_LOCATION_DATA;
