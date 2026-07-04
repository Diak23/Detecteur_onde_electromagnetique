#include <Arduino.h>
#line 1 "/home/projeteea/MY_PIC/MY_PIC.ino"
#include <WiFiNINA.h>
#include <WiFiUdp.h>

char ssid[] = "Galaxy S9+0105";
char pass[] = "Diakllo@";

WiFiUDP udp;

IPAddress raspberryIP(10,213,124,110);
unsigned int raspberryPort = 5005;
unsigned int localPort = 2390;

unsigned long compteur = 0;

String messages[] = {
  "MSG_1_COURT",
  "MSG_2_MOYEN_Bonjour_TEMPO",
  "MSG_3_LONG_Bonjour_TEMPO_Projet_Dosimetre_WiFi_2_4GHz",
  "MSG_4_TRES_LONG_ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZ",
  "MSG_5_MAX_ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZ"
};

int nombreMessages = 5;
int indexMessage = 0;

#line 26 "/home/projeteea/MY_PIC/MY_PIC.ino"
void setup();
#line 45 "/home/projeteea/MY_PIC/MY_PIC.ino"
void loop();
#line 26 "/home/projeteea/MY_PIC/MY_PIC.ino"
void setup() {
  Serial.begin(9600);
  while (!Serial);

  Serial.println("Initialisation...");
  Serial.println("Connexion au WiFi...");

  while (WiFi.begin(ssid, pass) != WL_CONNECTED) {
    Serial.println("Connexion en cours...");
    delay(3000);
  }

  Serial.println("Connecte au WiFi !");
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

  Serial.print("Envoye : ");
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

