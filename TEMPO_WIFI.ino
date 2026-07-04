#include <WiFiNINA.h>
#include <WiFiUdp.h>

char ssid[] = "Galaxy S9+0105";
char pass[] = "Diakllo23";

WiFiUDP udp;

IPAddress raspberryIP(192, 168, 1, 50);
unsigned int raspberryPort = 5005;

unsigned long compteur = 0;
char paquet[512];

void setup() {
  Serial.begin(9600);
  while (!Serial);

  Serial.println("Initialisation MKR WiFi 1010...");

  if (WiFi.status() == WL_NO_MODULE) {
    Serial.println("ERREUR : module WiFi non detecte !");
    while (true);
  }

  Serial.print("Firmware WiFi : ");
  Serial.println(WiFi.firmwareVersion());

  Serial.print("Connexion au reseau : ");
  Serial.println(ssid);

  int status = WL_IDLE_STATUS;

  while (status != WL_CONNECTED) {
    status = WiFi.begin(ssid, pass);

    Serial.print("Statut WiFi = ");
    Serial.println(status);

    delay(5000);
  }

  Serial.println("Connecte au WiFi !");
  Serial.print("IP Arduino : ");
  Serial.println(WiFi.localIP());

  for (int i = 0; i < sizeof(paquet) - 1; i++) {
    paquet[i] = 'A' + (i % 26);
  }
  paquet[sizeof(paquet) - 1] = '\0';
}

void loop() {
  udp.beginPacket(raspberryIP, raspberryPort);
  udp.print("TEMPO_WIFI_TRAFFIC_");
  udp.print(compteur);
  udp.print("_");
  udp.print(paquet);
  udp.endPacket();

  compteur++;

  if (compteur % 100 == 0) {
    Serial.print("Paquets envoyes : ");
    Serial.println(compteur);
  }

  delay(5);
}
