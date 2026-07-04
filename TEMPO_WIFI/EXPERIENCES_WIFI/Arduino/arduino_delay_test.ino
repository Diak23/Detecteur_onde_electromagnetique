#include <WiFiNINA.h>
#include <WiFiUdp.h>

char ssid[] = "Galaxy S9+0105";
char pass[] = "Diakllo@";

WiFiUDP udp;

IPAddress raspberryIP(10,213,124,110);
unsigned int raspberryPort = 5005;
unsigned int localPort = 2390;

unsigned long compteur = 0;

// À modifier pour chaque expérience
int delay_ms = 5;          // délai entre paquets
int taille_message = 512;  // taille du paquet
String nom_experience = "delay_5ms";

char paquet[1024];

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

  for (int i = 0; i < taille_message - 1; i++) {
    paquet[i] = 'A' + (i % 26);
  }

  paquet[taille_message - 1] = '\0';
}

void loop() {
  udp.beginPacket(raspberryIP, raspberryPort);

  udp.print(nom_experience);
  udp.print(",");
  udp.print(compteur);
  udp.print(",");
  udp.print(delay_ms);
  udp.print(",");
  udp.print(taille_message);
  udp.print(",");
  udp.print(paquet);

  int ok = udp.endPacket();

  compteur++;

  if (compteur % 100 == 0) {
    Serial.print("Experience : ");
    Serial.print(nom_experience);
    Serial.print(" | Paquets envoyes : ");
    Serial.print(compteur);
    Serial.print(" | endPacket = ");
    Serial.println(ok);
  }

  delay(delay_ms);
}
