#include <WiFiNINA.h>
#include <WiFiUdp.h>

char ssid[] = "NOM_DE_TON_WIFI";
char pass[] = "MOT_DE_PASSE_WIFI";

WiFiUDP udp;

// Adresse IP du Raspberry Pi
IPAddress raspberryIP(10, 213, 124, 110);

unsigned int raspberryPort = 5005;
unsigned int localPort = 2390;

unsigned long compteur = 0;

String messages[] = {
  "MSG_1_COURT",
  "MSG_2_MOYEN_Bonjour_TEMPO",
  "MSG_3_LONG_Bonjour_TEMPO_Projet_Dosimetre_WiFi_2_4GHz",
  "MSG_4_TRES_LONG_ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZ",
  "MSG_5_MAX_ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZ"
};

int nombreMessages = 5;
int indexMessage = 0;

void setup() {
  Serial.begin(9600);
  while (!Serial);

  Serial.println("Initialisation Arduino MKR WiFi 1010");
  Serial.println("Connexion au WiFi...");

  while (WiFi.begin(ssid, pass) != WL_CONNECTED) {
    Serial.println("Connexion en cours...");
    delay(3000);
  }

  Serial.println("Connecté au WiFi !");
  Serial.print("Adresse IP Arduino : ");
  Serial.println(WiFi.localIP());

  udp.begin(localPort);
}

void loop() {
  String message = messages[indexMessage];

  String paquet = "PAQUET,";
  paquet += compteur;
  paquet += ",";
  paquet += "TAILLE,";
  paquet += message.length();
  paquet += ",";
  paquet += message;

  udp.beginPacket(raspberryIP, raspberryPort);
  udp.print(paquet);
  int ok = udp.endPacket();

  Serial.print("Envoyé : ");
  Serial.print(paquet);
  Serial.print(" | endPacket = ");
  Serial.println(ok);

  compteur++;

  indexMessage++;
  if (indexMessage >= nombreMessages) {
    indexMessage = 0;
  }

  delay(1000);
}
