#include <Arduino.h>
#line 1 "/home/projeteea/acquisition_udp/acquisition_udp.ino"
#include <WiFiNINA.h>
#include <WiFiUdp.h>

char ssid[] = "Galaxy S9+0105";
char pass[] = "Diakllo23";

WiFiUDP udp;

// Adresse IP du Raspberry Pi
IPAddress raspberryIP(10, 138, 218, 110);

unsigned int raspberryPort = 5005;
unsigned int localPort = 2390;

unsigned long compteur = 0;

String messages[] = {
 "A",
};

int nombreMessages = 1;
int indexMessage = 0;

#line 24 "/home/projeteea/acquisition_udp/acquisition_udp.ino"
void setup();
#line 43 "/home/projeteea/acquisition_udp/acquisition_udp.ino"
void loop();
#line 24 "/home/projeteea/acquisition_udp/acquisition_udp.ino"
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




