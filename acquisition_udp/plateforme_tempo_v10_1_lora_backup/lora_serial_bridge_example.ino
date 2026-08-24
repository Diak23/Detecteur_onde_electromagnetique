/* Exemple minimal de trame JSON envoyée au Raspberry Pi. */
void setup(){ Serial.begin(115200); }
void loop(){
  Serial.println("{\"frequency_mhz\":868.1,\"rssi_dbm\":-83,\"snr_db\":7.5,\"sf\":7,\"bw_khz\":125,\"cr\":\"4/5\",\"length\":5,\"crc_ok\":true,\"payload_hex\":\"48454c4c4f\"}");
  delay(5000);
}
