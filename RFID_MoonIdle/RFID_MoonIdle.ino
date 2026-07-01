#include <SPI.h>
#include <MFRC522.h>

#define SS_PIN 10
#define RST_PIN 9

MFRC522 mfrc522(SS_PIN, RST_PIN);

// Hier Tag UID

byte uid1[] = {0x1D, 0x69, 0x56, 0x62, 0x0D, 0x10, 0x80};

bool compareUID(byte *tag, byte *ref, byte length) {
  for (byte i = 0; i < length; i++) {
    if (tag[i] != ref[i]) return false;
  }
  return true;
}

int lastValue = -1;      // letzter gesendeter Wert (0,1,2,3)
int noCardCount = 0;     // wie oft hintereinander keine Karte gesehen

void setup() {
  Serial.begin(9600);
  SPI.begin();
  mfrc522.PCD_Init();
  Serial.println("Ready to scan!");
}

void loop() {

  int currentValue = lastValue;

  // --- Try to read a card ---
  if (!mfrc522.PICC_IsNewCardPresent() || !mfrc522.PICC_ReadCardSerial()) {

    // No card detected
    if (lastValue != 1) {
      noCardCount++;

      if (noCardCount >= 5) {
        currentValue = 1;   // No card = 1
        noCardCount = 0;
      }
    }

  } else {

    // Card detected
    noCardCount = 0;

    byte *tagUID = mfrc522.uid.uidByte;
    byte uidSize = mfrc522.uid.size;

    if (compareUID(tagUID, uid1, uidSize)) {
      currentValue = 0;     // Known tag = 0
    }
    else {
      currentValue = 1;     // Unknown tag = 1
    }
  }

  // Only send when state changes
  if (currentValue != lastValue) {
    Serial.println(currentValue);
    lastValue = currentValue;
  }

  delay(50);
}

