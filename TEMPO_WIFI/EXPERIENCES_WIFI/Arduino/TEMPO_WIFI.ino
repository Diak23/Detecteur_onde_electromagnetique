#include <WiFiNINA.h>
#include <WiFiUdp.h>

char ssid[] = "Galaxy S9+0105";
char pass[] = "Diakllo23";

WiFiUDP udp;

// Remplace par l'adresse IP de ta Raspberry Pi


IPAddress raspberryIP(10, 213, 124, 110);
unsigned int raspberryPort = 5005;
unsigned int localPort = 2390;

unsigned long compteur = 0;
char paquet[512];

void setup() {
  Serial.begin(9600);
  while (!Serial);

  Serial.println("Initialisation...");
  Serial.println("Connexion au WiFi...");

  while (WiFi.begin(ssid, pass) != WL_CONNECTED) {
    Serial.println("Connexion en cours...");
    delay(3000);
  }

  Serial.println("Connecté au WiFi !");
  Serial.print("Adresse IP Arduino : ");
  Serial.println(WiFi.localIP());

  udp.begin(localPort);

  for (int i = 0; i < sizeof(paquet) - 1; i++) {
    paquet[i] = 'A' + (i % 26);
  }
  paquet[sizeof(paquet) - 1] = '\0';
}

void loop() {
  String message = "Bonjour TEMPO, message envoye par WiFi";

  udp.beginPacket(raspberryIP, raspberryPort);
  udp.print(message);
  int ok = udp.endPacket();

  Serial.print("Message envoye : ");
  Serial.print(message);
  Serial.print(" | endPacket = ");
  Serial.println(ok);

  delay(1000);
}


