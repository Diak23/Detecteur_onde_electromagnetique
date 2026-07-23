from __future__ import annotations


class MCP3208Reader:
    def __init__(self, bus: int = 0, device: int = 0, max_speed_hz: int = 1_000_000):
        self.bus = bus
        self.device = device
        self.max_speed_hz = max_speed_hz
        self.spi = None

    def open(self) -> None:
        try:
            import spidev
        except ImportError as exc:
            raise RuntimeError(
                "Le module spidev n'est pas installé. Lancez ./install.sh"
            ) from exc

        self.spi = spidev.SpiDev()
        self.spi.open(self.bus, self.device)
        self.spi.max_speed_hz = self.max_speed_hz
        self.spi.mode = 0b00

    def read_channel(self, channel: int) -> int:
        if self.spi is None:
            raise RuntimeError("Le bus SPI n'est pas ouvert.")
        if channel < 0 or channel > 7:
            raise ValueError("Le canal MCP3208 doit être compris entre 0 et 7.")

        command_1 = 0b00000110 | ((channel & 0b100) >> 2)
        command_2 = (channel & 0b011) << 6
        response = self.spi.xfer2([command_1, command_2, 0x00])
        return ((response[1] & 0x0F) << 8) | response[2]

    def close(self) -> None:
        if self.spi is not None:
            self.spi.close()
            self.spi = None
