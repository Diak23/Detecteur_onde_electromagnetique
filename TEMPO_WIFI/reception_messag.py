import socket

UDP_IP = "0.0.0.0"
UDP_PORT = 5005


sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

print("En attente des messages...")


while True:
    data, addr = sock.recvfrom(1024)


    message=  data.decode()

    print("------------------------")
    print("Source :", addr)
    print("Message:", message)


