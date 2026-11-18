"""Model-, Sensor- und Dongle-Katalog fuer ATK / VXE / Compx Maeuse.

Der Katalog stammt aus dem offenen OpenMouse-Protokoll-Katalog
(ATK HUB 3.2.x Geraetetabelle) und enthaelt alle bekannten
VID:PID-Kombinationen der Marken ATK, VXE und Compx (373b / 3554).

Wichtig: Die USB Product-ID ist **nicht** eindeutig. Dongle-PIDs wie
0x101a (1K), 0x101b (8K) oder 0x10c9 (NearLink) werden von vielen
Modellen benutzt. Die eindeutige Identifikation laeufer ueber die
CID/MID-Abfrage (Kommando 0x10).
"""

from __future__ import annotations

from dataclasses import dataclass

from .protocol import DpiCodec, LodTable, PAW3950_LOD, STEP_LOD, ULTRA_LOD, make_dpi_codec

VENDOR_IDS: tuple[int, ...] = (0x373B, 0x3554)


@dataclass(frozen=True)
class Sensor:
    name: str
    family: str
    min_dpi: int
    max_dpi: int
    step: int = 100
    lod: LodTable = ULTRA_LOD
    max_polling_hz: int = 8000
    supports_xy_dpi: bool = True

    def codec(self) -> DpiCodec:
        return make_dpi_codec(self.family, self.min_dpi, self.max_dpi, self.step)


SENSORS: dict[str, Sensor] = {
    "PAW3950Ultra": Sensor("PAW3950Ultra", "ultra", 10, 42000, lod=ULTRA_LOD),
    "PAW3950": Sensor("PAW3950", "step50", 50, 36000, lod=PAW3950_LOD),
    "PAW3950DM": Sensor("PAW3950DM", "step50", 50, 36000, lod=PAW3950_LOD),
    "PAW3395Ultra": Sensor("PAW3395Ultra", "step50", 100, 30000, lod=STEP_LOD),
    "PAW3395": Sensor("PAW3395", "step50", 100, 30000, lod=STEP_LOD),
    "PAW3395SE": Sensor("PAW3395SE", "paw3395se", 200, 18000, lod=STEP_LOD),
    "PAW3315": Sensor("PAW3315", "stepped", 200, 16000, step=100, lod=STEP_LOD),
    "PAW3311": Sensor("PAW3311", "stepped", 200, 15000, step=100, lod=STEP_LOD),
    "PAW3320": Sensor("PAW3320", "stepped", 200, 4000, step=39, lod=STEP_LOD),
    "CORE26K": Sensor("CORE26K", "step50", 50, 26000, lod=STEP_LOD),
    # PAW3955Master benutzt ein anderes 8-Byte-Stage-Format (nicht implementiert)
    "PAW3955Master": Sensor("PAW3955Master", "paw3955master", 50, 40000, lod=STEP_LOD),
    "unknown": Sensor("unknown", "ultra", 10, 42000, lod=ULTRA_LOD),
}

# Aufsteck-/Empfaenger-Module (Dongles): PID ist nicht modellbezogen.
RECEIVERS: dict[int, str] = {
    0x101A: "ATK Mouse 1K Dongle (max. 1000 Hz)",
    0x101B: "ATK Mouse 8K Dongle (max. 8000 Hz)",
    0x10C9: "ATK/NK Mouse Dongle (NearLink, max. 1000 Hz)",
    0x11FE: "ATK Dongle (8K Tri-Mode)",
}

RECEIVER_MAX_POLLING: dict[int, int] = {
    0x101A: 1000,
    0x101B: 8000,
    0x10C9: 1000,
    0x11FE: 8000,
}

ALL_POLLING_RATES: tuple[int, ...] = (125, 250, 500, 1000, 2000, 4000, 8000)


def polling_options(product_id: int | None, model_max: int | None = None) -> tuple[int, ...]:
    """Polling-Raten, die Empfaenger (und ggf. Modell) zulassen, aufsteigend."""
    limit = RECEIVER_MAX_POLLING.get(product_id or 0) or model_max or 8000
    return tuple(rate for rate in ALL_POLLING_RATES if rate <= limit)


@dataclass(frozen=True)
class Model:
    vendor: int
    product: int
    brand: str
    name: str
    sensor: str
    cid_mid: str | None
    usage_page: int
    usage: int
    features: frozenset[str] = frozenset()

    @property
    def sensor_info(self) -> Sensor:
        return SENSORS.get(self.sensor, SENSORS["unknown"])

    @property
    def dpi_family(self) -> str:
        return self.sensor_info.family

    @property
    def dpi_min(self) -> int:
        return self.sensor_info.min_dpi

    @property
    def dpi_max(self) -> int:
        return self.sensor_info.max_dpi

    @property
    def lod_table(self) -> LodTable:
        return self.sensor_info.lod

    @property
    def vidpid(self) -> str:
        return f"{self.vendor:04x}:{self.product:04x}"

    def supports(self, feature: str) -> bool:
        return feature not in self.features

    @property
    def is_receiver(self) -> bool:
        return self.product in RECEIVERS

    def max_polling_hz(self, receiver_product: int | None = None) -> int:
        limit = self.sensor_info.max_polling_hz
        if receiver_product is not None:
            limit = min(limit, RECEIVER_MAX_POLLING.get(receiver_product, limit))
        return limit

    def polling_options(self, receiver_product: int | None = None) -> tuple[int, ...]:
        limit = self.max_polling_hz(receiver_product)
        return tuple(hz for hz in (125, 250, 500, 1000, 2000, 4000, 8000) if hz <= limit)

    def describe(self) -> str:
        feats = ", ".join(sorted(self.features)) or "-"
        return (
            f"{self.brand} {self.name} [{self.vidpid}] Sensor={self.sensor} "
            f"CID/MID={self.cid_mid or '-'} Merkmale: {feats}"
        )


# ---------------------------------------------------------------------------
# Geratetabelle: vid|pid|brand|model|sensor|cid,mid|usagePage|usage|features
# Quelle: OpenMouse-Project/mouse-protocol device-catalog (ATK HUB 3.2.x)
# ---------------------------------------------------------------------------

_RAW_MODELS: tuple[str, ...] = (
    "3554|f502|ATK|ATK Dragonfly F1 PRO|PAW3395|2,1|65284|2|identifyByCidMid",
    "3554|f506|ATK|ATK Dragonfly F1 PRO MAX|PAW3395|2,3|65284|2|identifyByCidMid",
    "3554|f506|ATK|ATK Dragonfly F1 MOBA|PAW3395|2,5|65284|2|identifyByCidMid",
    "3554|f506|ATK|ATK Dragonfly F1 PRO MAX Elden|PAW3395|2,48|65284|2|identifyByCidMid",
    "3554|f506|ATK|ATK Dragonfly F1 PRO MAX JOJO|PAW3395|2,52|65284|2|identifyByCidMid",
    "3554|f50a|ATK|ATK Dragonfly Dragonfly F1S|PAW3395|2,20|65284|2|identifyByCidMid",
    "3554|f50a|ATK|ATK Dragonfly F1S|PAW3395|2,19|65284|2|identifyByCidMid",
    "3554|f58c|VXE|VXE R1 PRO MAX|PAW3395|2,27|65284|2|identifyByCidMid",
    "3554|f58c|VXE|VXE R1 PRO|PAW3395|2,7|65284|2|identifyByCidMid",
    "3554|f58f|VXE|VXE R1SE+|PAW3395SE|2,31|65284|2|identifyByCidMid",
    "3554|f58f|VXE|VXE R1|PAW3395|2,11|65284|2|identifyByCidMid",
    "3554|f58f|VXE|VXE R1SE|PAW3395SE|2,29|65284|2|identifyByCidMid",
    "373b|1014|ATK|ATK F1 PRO|PAW3950|2,36|65284|2|-",
    "373b|1015|ATK|ATK F1 PRO MAX|PAW3950|2,37|65284|2|-",
    "373b|1016|ATK|ATK X1 PRO|PAW3950|2,38|65284|2|-",
    "373b|1017|ATK|ATK X1 PRO MAX|PAW3950|2,39|65284|2|-",
    "373b|1018|ATK|ATK Z1 PRO|PAW3950|2,40|65284|2|-",
    "373b|1019|ATK|ATK Z1 PRO MAX|PAW3950|2,41|65284|2|-",
    "373b|102e|ATK|ATK F1 Ultimate|PAW3950Ultra|2,42|65284|2|noBottomButton,noDpiRGB",
    "373b|102f|ATK|ATK X1 Ultra|PAW3950Ultra|2,43|65284|2|-",
    "373b|1030|ATK|ATK FIERCE X|PAW3950Ultra|2,44|65284|2|-",
    "373b|103c|ATK|ATK F1 Ultra|PAW3950Ultra|2,45|65284|2|noBottomButton,noDpiRGB",
    "373b|103d|ATK|ATK X1 Ultimate|PAW3950Ultra|2,46|65284|2|noBottomButton",
    "373b|103e|ATK|MAD R MAJOR|PAW3950|2,47|65284|2|noBottomButton",
    "373b|103f|ATK|MAD R|PAW3395|2,49|65284|2|noBottomButton",
    "373b|1045|ATK|ATK F1 EXTREME|PAW3950Ultra|2,50|65284|2|noBottomButton,noDpiRGB",
    "373b|104a|ATK|ATK Z1 Ultra|PAW3950Ultra|2,53|65284|2|-",
    "373b|104b|ATK|MAD R +|PAW3395|2,54|65284|2|noBottomButton",
    "373b|104c|ATK|MAD R MAJOR+|PAW3950|2,55|65284|2|noBottomButton",
    "373b|105b|ATK|ATK F1 LEVIATAN|PAW3950Ultra|2,56|65284|2|noBottomButton",
    "373b|1065|VXE|VXE R1 NK|PAW3395SE|2,57|65284|2|noFarDistance",
    "373b|1066|VXE|VXE R1 NK|PAW3395SE|2,57|65284|2|noFarDistance",
    "373b|1084|VXE|VXE R1S+|PAW3311|2,63|65284|2|-",
    "373b|1086|ATK|ATK U2 MINI|PAW3950|2,64|65284|2|-",
    "373b|1087|ATK|ATK U2 Ultimate|PAW3950Ultra|2,65|65284|2|-",
    "373b|10ac|ATK|ATK U2 Ultra|PAW3950|2,66|65284|2|-",
    "373b|10c7|VXE|VXE R1 NK|PAW3395SE|2,67|65284|2|noFarDistance",
    "373b|10c8|VXE|VXE R1 NK|PAW3395SE|2,67|65284|2|noFarDistance",
    "373b|10d8|ATK|ATK X1|PAW3395|1,1|65296|1|noZeroKeyDebounce",
    "373b|10e7|VXE|VXE V3 NK|PAW3311|2,72|65284|2|middleDpiButton,noFarDistance,noShowPower",
    "373b|10e8|VXE|VXE V3 PRO NK|PAW3395SE|2,73|65284|2|middleDpiButton,noFarDistance,noShowPower",
    "373b|10e9|ATK|ATK X1 EXTREME|PAW3950Ultra|2,70|65284|2|-",
    "373b|10ec|ATK|ATK U2 Pro|PAW3395Ultra|2,1|65296|1|noZeroKeyDebounce",
    "373b|10fd|ATK|ATK Y9 PRO MAX|PAW3950|2,108|65284|2|noDpiRGB",
    "373b|1106|ATK|ATK X1 S NK|PAW3395|2,76|65284|2|noFarDistance",
    "373b|1107|ATK|ATK X1 SE NK|PAW3395SE|2,77|65284|2|noFarDistance",
    "373b|110d|ATK|ATK U2 Plus NK|PAW3395|2,78|65284|2|noFarDistance",
    "373b|110e|ATK|ATK U2 NK|PAW3395SE|2,79|65284|2|noFarDistance",
    "373b|1115|ATK|ATK A9 Plus NK|PAW3395|2,83|65284|2|noDpiRGB,noFarDistance",
    "373b|1116|ATK|ATK A9 NK|PAW3395SE|2,84|65284|2|noDpiRGB,noFarDistance",
    "373b|1117|ATK|ATK A9 Pro Max|PAW3395Ultra|2,85|65284|2|noDpiRGB",
    "373b|1119|ATK|ATK A9 Ultra|PAW3950Ultra|2,87|65284|2|noDpiRGB",
    "373b|112d|ATK|ATK A9 Pro|PAW3395Ultra|2,2|65296|1|noDpiRGB,noZeroKeyDebounce",
    "373b|112e|ATK|ATK Y9 Pro|PAW3395Ultra|2,3|65296|1|noZeroKeyDebounce",
    "373b|1135|ATK|ATK A9 SE NK|PAW3311|2,88|65284|2|noDpiRGB,noFarDistance",
    "373b|113e|ATK|ATK X1 Lite|PAW3395|2,89|65284|2|noFarDistance",
    "373b|113f|ATK|ATK X1E|PAW3395SE|2,90|65284|2|-",
    "373b|1147|ATK|ATK A9 PRO MAX|PAW3395Ultra|0,0|65440|2|noDpiRGB",
    "373b|1149|ATK|ATK U2 SE NK|PAW3311|2,92|65284|2|noFarDistance",
    "373b|114a|ATK|DRAGONFLY F1 V2 Plus NK|PAW3395|2,93|65284|2|noFarDistance",
    "373b|114b|ATK|DRAGONFLY F1 V2 NK|PAW3395SE|2,94|65284|2|noFarDistance",
    "373b|114c|ATK|DRAGONFLY F1 V2 SE NK|PAW3311|2,95|65284|2|noFarDistance",
    "373b|1154|ATK|ATK ZERO|PAW3950Ultra|1,1|65285|1|noBottomButton,noDpiRGB",
    "373b|1157|ATK|ATK A9|PAW3395SE|2,99|65284|2|noDpiRGB",
    "373b|1158|ATK|ATK U2 Pro Max|PAW3395Ultra|2,100|65284|2|-",
    "373b|115b|ATK|ATK Y9 Plus NK|PAW3395|2,96|65284|2|noFarDistance",
    "373b|115c|ATK|ATK Y9 NK|PAW3395SE|2,97|65284|2|noFarDistance",
    "373b|115d|ATK|ATK Y9 SE NK|PAW3311|2,98|65284|2|noFarDistance",
    "373b|1175|ATK|DRAGONFLY F1 V2 Pro|PAW3395Ultra|4,2|65296|1|noZeroKeyDebounce",
    "373b|11a0|ATK|ATK A9 X|PAW3395SE|2,107|65284|2|-",
    "373b|11a6|ATK|ATK Duckbill Ultra|PAW3950Ultra|1,2|65284|2|is54H20",
    "373b|11aa|ATK|ATK U2|PAW3395SE|2,109|65284|2|-",
    "373b|11ab|ATK|DRAGONFLY F1 V2 PRO MAX|PAW3395Ultra|2,110|65284|2|-",
    "373b|11b6|ATK|ATK A9 Ultimate|PAW3950Ultra|1,3|65284|2|noDpiRGB",
    "373b|11cc|ATK|ATK ZERO EX|PAW3950Ultra|1,4|65284|2|is54H20,noBottomButton,noDpiRGB",
    "373b|11cf|ATK|ATK Y9 Ultimate|PAW3950Ultra|1,5|65284|2|noDpiRGB",
    "373b|11d2|VXE|VXE R1 UlTRA|PAW3395|2,114|65284|2|-",
    "373b|11db|ATK|ATK A9 Pro Max 2.0 NK|PAW3395Ultra|2,1|65285|1|isNearlink,noDpiRGB,noFarDistance,noZeroKeyDebounce",
    "373b|11dd|ATK|ATK A9 Ultra Max 2.0|PAW3395Ultra|1,6|65284|2|noDpiRGB,noZeroKeyDebounce",
    "373b|11e3|ATK|ATK F1 EXTREME 2.0|PAW3950Ultra|1,7|65284|2|is54H20,noBottomButton,noDpiRGB",
    "373b|11e4|ATK|ATK F1 Ultimate 2.0|PAW3950Ultra|1,8|65284|2|noBottomButton,noDpiRGB",
    "373b|11e5|ATK|ATK X1 EXTREME 2.0|PAW3950Ultra|1,9|65284|2|is54H20,noBottomButton",
    "373b|11e7|ATK|ATK X1 Ultimate 2.0|PAW3950Ultra|1,10|65284|2|noBottomButton",
    "373b|11ec|ATK|ATK X1 Ultra Max 2.0|PAW3395Ultra|1,11|65284|2|-",
    "373b|11ed|ATK|ATK F1 Ultra Max 2.0|PAW3395Ultra|1,12|65284|2|noDpiRGB",
    "373b|11f0|ATK|ATK F1 LEVIATAN 2.0|PAW3950Ultra|1,13|65284|2|is54H20,noBottomButton,noDpiRGB",
    "373b|11f2|ATK|ATK A9 Ultra Max 2.0 Tri-Mode|PAW3395Ultra|1,15|65284|2|noDpiRGB,noZeroKeyDebounce",
    "373b|11f3|ATK|ATK A9 NB ULTRA|PAW3950Ultra|2,115|65284|2|noDpiRGB",
    "373b|11f5|ATK|ATK U2 Ultimate 2.0|PAW3950Ultra|1,16|65284|2|-",
    "373b|11f6|ATK|ATK U2 Mini 2.0|PAW3950Ultra|1,17|65284|2|-",
    "373b|11f7|ATK|ATK U2 Ultra Max 2.0|PAW3395Ultra|1,18|65284|2|-",
    "373b|11f9|VXE|VXE R1 Ultra|PAW3395Ultra|1,20|65284|2|-",
    "373b|1201|ATK|ATK Z1 Ultimate 2.0|PAW3950Ultra|1,19|65284|2|-",
    "373b|120a|ATK|ATK A9 Air|PAW3950Ultra|1,21|65284|2|noDpiRGB",
    "373b|120b|ATK|ATK X1 EXTREME GT|PAW3950Ultra|1,22|65284|2|is54H20,noBottomButton",
    "373b|120d|ATK|ATK Ghost EXTREME|PAW3950Ultra|1,23|65284|2|is54H20",
    "373b|120e|ATK|ATK Ghost Ultimate|PAW3950Ultra|1,24|65284|2|-",
    "373b|1211|ATK|ATK A9 Ultimate 3.0|PAW3950Ultra|1,2|65285|1|noDpiRGB",
    "373b|1213|VXE|VXE V3 NK|PAW3311|2,5|65285|1|isNearlink,middleDpiButton,noAthleticsMax,noDpiRGB,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|1214|VXE|VXE V3 PRO NK|PAW3395SE|2,6|65285|1|isNearlink,middleDpiButton,noAthleticsMax,noDpiRGB,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|1215|VXE|VXE V3 Plus|PAW3395Ultra|2,7|65285|1|isNearlink,middleDpiButton,noAthleticsMax,noDpiRGB,noFarDistance,noSensorAngle,noZeroKeyDebounce",
    "373b|121f|VXE|VXE R1 S NK|PAW3311|2,117|65284|2|noFarDistance",
    "373b|1222|ATK|ATK A9 SE NK|PAW3311|2,2|65285|1|isNearlink,noAthleticsMax,noDpiRGB,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|1223|ATK|ATK A9 NK|PAW3395SE|2,3|65285|1|isNearlink,noAthleticsMax,noDpiRGB,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|1224|ATK|ATK A9 Plus NK|PAW3395Ultra|2,4|65285|1|isNearlink,noAthleticsMax,noDpiRGB,noFarDistance",
    "373b|1230|ATK|ATK Duckbill EXTREME|PAW3950Ultra|1,25|65284|2|is54H20",
    "373b|123a|ATK|ATK Ghost Ultimate|PAW3950Ultra|1,3|65285|1|noDpiRGB",
    "373b|1245|ATK|ATK A9 NB|PAW3395SE|2,120|65284|2|noDpiRGB",
    "373b|1248|VXE|VXE X3|PAW3315|2,121|65284|2|-",
    "373b|1249|VXE|VXE X3 Pro|PAW3395SE|2,122|65284|2|-",
    "373b|124a|VXE|VXE X3 Turbo|PAW3395SE|2,123|65284|2|-",
    "373b|124c|VXE|VXE R1 NK|PAW3395SE|2,8|65285|1|isNearlink,noAthleticsMax,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|124d|VXE|VXE R1 S NK|PAW3311|2,9|65285|1|isNearlink,noAthleticsMax,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|124e|ATK|ATK ZERO|PAW3950Ultra|1,26|65284|2|noBottomButton",
    "373b|1251|ATK|ATK A9 Mini +|PAW3955Master|1,4|65285|1|noDpiRGB",
    "373b|1256|ATK|ATK A9 Mini|PAW3950Ultra|1,27|65284|2|noDpiRGB",
    "373b|125a|ATK|ATK A9 Mini|PAW3950Ultra|1,28|65284|2|noDpiRGB",
    "373b|1263|ATK|ATK A9 Plus NK|PAW3950Ultra|2,13|65285|1|isNearlink,noAthleticsMax,noDpiRGB,noFarDistance,noZeroKeyDebounce",
    "373b|1264|ATK|ATK A9 Pro Max 2.0 NK|PAW3950Ultra|2,14|65285|1|isNearlink,noDpiRGB,noFarDistance,noZeroKeyDebounce",
    "373b|1266|ATK|ATK X EXTREME|PAW3950Ultra|1,30|65284|2|is54H20,noBottomButton",
    "373b|1268|ATK|ATK A9 Mini|PAW3950Ultra|1,16|65285|1|noDpiRGB",
    "373b|1269|ATK|ATK A9 Mini +|PAW3955Master|1,31|65284|2|noDpiRGB",
    "373b|126c|ATK|ATK U2 Plus NK|PAW3950Ultra|2,124|65284|2|noFarDistance",
    "373b|126d|ATK|ATK U2 Pro Max|PAW3950Ultra|2,128|65284|2|-",
    "373b|126e|ATK|ATK F1 Ultra Max 2.0|PAW3950Ultra|1,33|65284|2|noDpiRGB",
    "373b|126f|ATK|ATK A9 Ultra Max 2.0|PAW3950Ultra|1,38|65284|2|noDpiRGB,noZeroKeyDebounce",
    "373b|1270|ATK|ATK A9 Ultra Max 2.0 Tri-Mode|PAW3950Ultra|1,37|65284|2|noDpiRGB,noZeroKeyDebounce",
    "373b|1271|VXE|VXE R1|PAW3950Ultra|2,127|65284|2|-",
    "373b|1272|VXE|VXE R1 PRO|PAW3950Ultra|2,125|65284|2|-",
    "373b|1273|VXE|VXE R1 PRO MAX|PAW3950Ultra|2,126|65284|2|-",
    "373b|1274|ATK|ATK X1 Ultra Max 2.0|PAW3950Ultra|1,32|65284|2|-",
    "373b|127b|VXE|VXE V3 PRO Max|CORE26K|2,15|65285|1|isNearlink,middleDpiButton,noAthleticsMax,noDpiRGB,noFarDistance,noSensorAngle,noZeroKeyDebounce",
    "373b|127c|ATK|ATK X1 Air +|PAW3955Master|1,42|65284|2|is54H20,noBottomButton,supportDpiToggle",
    "373b|127e|ATK|ATK A9 Ultimate +|PAW3955Master|1,39|65284|2|noDpiRGB",
    "373b|127f|ATK|ATK A9 Air +|PAW3955Master|1,40|65284|2|noDpiRGB",
    "373b|1282|ATK|ATK Duckbill ELITE|PAW3950Ultra|`${Rt.WAIZOWL},1`|65282|2|noDpiRGB,noFarDistance",
    "373b|1285|ATK|ATK X1 EXTREME 2.0 +|PAW3955Master|1,45|65284|2|is54H20,noBottomButton,supportDpiToggle",
    "373b|1286|ATK|ATK X1 Ultimate 2.0 +|PAW3955Master|1,46|65284|2|noBottomButton,supportDpiToggle",
    "373b|1287|ATK|ATK A9 Ultimate +|PAW3955Master|1,9|65285|1|noDpiRGB",
    "373b|1288|ATK|ATK A9 Air +|PAW3955Master|1,6|65285|1|noDpiRGB",
    "373b|1289|ATK|ATK ZERO EX +|PAW3955Master|1,47|65284|2|is54H20,noBottomButton,noDpiRGB,supportDpiToggle",
    "373b|128a|ATK|ATK Ghost EXTREME +|PAW3955Master|1,48|65284|2|is54H20",
    "373b|128b|ATK|ATK Ghost Ultimate +|PAW3955Master|1,49|65284|2|-",
    "373b|128c|ATK|ATK U2 Ultimate 2.0 +|PAW3955Master|1,50|65284|2|-",
    "373b|128d|ATK|ATK U2 Mini 2.0 +|PAW3955Master|1,51|65284|2|-",
    "373b|128e|ATK|ATK A9 Mini +|PAW3955Master|1,52|65284|2|noDpiRGB",
    "373b|128f|ATK|ATK A9 Ultra Max 2.0 +|PAW3950Ultra|1,7|65285|1|noDpiRGB",
    "373b|1292|ATK|ATK A9 Ultimate +|PAW3955Master|1,53|65284|2|noDpiRGB",
    "373b|1293|ATK|ATK A9 Air +|PAW3955Master|1,54|65284|2|noDpiRGB",
    "373b|1296|ATK|ATK PHANTOM ULTIMATE|PAW3955Master|1,55|65284|2|noBottomButton,noDpiRGB,supportDpiToggle",
    "373b|1299|ATK|ATK X1 SE Nearlink|PAW3395SE|2,16|65285|1|isNearlink,noAthleticsMax,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|129a|VXE|VXE R1 PRO MAX 2.0 NK|PAW3950Ultra|2,17|65285|1|isNearlink,noAthleticsMax,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|129b|ATK|ATK F1 V3 EXTREME +|PAW3955Master|1,58|65284|2|is54H20,noBottomButton,noDpiRGB,noShowPower,supportDpiToggle",
    "373b|129c|ATK|ATK F1 Ultimate 2.0 +|PAW3955Master|1,59|65284|2|noBottomButton,noDpiRGB,supportDpiToggle",
    "373b|129d|ATK|ATK F1 V3 LEVIATAN +|PAW3955Master|1,60|65284|2|is54H20,noBottomButton,noDpiRGB,noShowPower,supportDpiToggle",
    "373b|129e|ATK|ATK ZERO +|PAW3955Master|1,8|65285|1|noBottomButton,noDpiRGB",
    "373b|12a2|ATK|ATK X1 Air +|PAW3955Master|1,61|65284|2|noBottomButton,supportDpiToggle",
    "373b|12a7|ATK|ATK F1 V3 ULTIMATE +|PAW3955Master|1,62|65284|2|noBottomButton,noDpiRGB,supportDpiToggle",
    "373b|12a8|ATK|ATK F1 V3 ULTIMATE +|PAW3955Master|1,63|65284|2|noBottomButton,noDpiRGB,supportDpiToggle",
    "373b|12a9|ATK|ATK F1 V3 ULTIMATE +|PAW3955Master|1,64|65284|2|noBottomButton,noDpiRGB,supportDpiToggle",
    "373b|12ab|ATK|ATK A9 NB ULTRA +|PAW3955Master|2,129|65284|2|noDpiRGB",
    "373b|12af|ATK|ATK Y9 Ultimate +|PAW3955Master|1,66|65284|2|noDpiRGB",
    "373b|12b0|ATK|ATK Z1 Ultimate 2.0 +|PAW3955Master|1,67|65284|2|-",
    "373b|12b1|VXE|VXE R2 SE NK|PAW3315|2,19|65285|1|isNearlink,noAthleticsMax,noFarDistance,noSensorAngle,noXyDpi",
    "373b|12b3|ATK|ATK A9 Ultimate Silent|PAW3955Master|1,75|65284|2|noDpiRGB,supportDpiToggle",
    "373b|12b4|ATK|ATK X1 Ultimate 2.0 Silent|PAW3955Master|1,76|65284|2|noBottomButton,supportDpiToggle",
    "373b|12bf|VXE|VXE R1SE+ Silent|PAW3395SE|2,130|65284|2|noFarDistance",
    "373b|12c6|ATK|ATK A9 Wireless Charging|PAW3955Master|1,71|65284|2|noDpiRGB",
    "373b|12ce|ATK|ATK PHANTOM ULTIMATE|PAW3955Master|1,10|65285|1|noBottomButton,noDpiRGB",
    "373b|12d0|ATK|ATK A9 NB ULTRA|PAW3950Ultra|2,20|65285|1|isNearlink,noFarDistance",
    "373b|12d2|VXE|VXE V3 NK|PAW3315|2,21|65285|1|isNearlink,middleDpiButton,noAthleticsMax,noDpiRGB,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|12d7|ATK|Flow 7|PAW3950Ultra|2,131|0|0|-",
    "373b|12d9|ATK|ATK X1 ULTIMATE 2.0+|PAW3955Master|1,12|65285|1|noBottomButton,noDpiRGB",
    "373b|12dc|VXE|VXE R1 SE+|CORE26K|2,22|65285|1|isNearlink,noAthleticsMax,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|12de|ATK|ATK X1 Ultra Max 2.0 + Tri-Mode|PAW3955Master|1,13|65285|1|noBottomButton,noDpiRGB",
    "373b|12e1|VXE|VXE T1|PAW3950Ultra|2,23|65285|1|isNearlink,noFarDistance",
    "373b|12e3|ATK|ATK U2 Plus NK|PAW3950Ultra|2,25|65285|1|isNearlink,noFarDistance,noZeroKeyDebounce",
    "373b|12ec|ATK|ATK X1 Air+|PAW3955Master|1,14|65285|1|noBottomButton,noDpiRGB",
    "373b|12f1|ATK|ATK A9 Ultimate +|PAW3955Master|1,15|65285|1|noDpiRGB",
    "373b|12fa|ATK|ATK F1 V3 ULTIMATE|PAW3950Ultra|1,85|65284|2|noBottomButton,noDpiRGB",
    "373b|12ff|ATK|ATK X1 Ultra Max 2.0|PAW3950DM|1,87|65284|2|-",
    "373b|1300|ATK|ATK A9 Plus NK|PAW3950DM|2,26|65285|1|isNearlink,noAthleticsMax,noDpiRGB,noFarDistance",
    "373b|1301|ATK|ATK A9 Pro Max 2.0 NK|PAW3950DM|2,27|65285|1|isNearlink,noDpiRGB,noFarDistance,noZeroKeyDebounce",
    "373b|1304|ATK|ATK F1 V3 ULTIMATE+|PAW3955Master|1,17|65285|1|noBottomButton,noDpiRGB",
    "373b|1305|ATK|ATK A9 3.0 NK|CORE26K|2,29|65285|1|isNearlink,noAthleticsMax,noDpiRGB,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|1306|VXE|VXE V3 PRO NK|CORE26K|2,30|65285|1|isNearlink,middleDpiButton,noAthleticsMax,noDpiRGB,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|1307|ATK|ATK F1 V3 Ultra Max|PAW3950Ultra|1,19|65285|1|-",
    "373b|1308|ATK|ATK F1 V3 ULTIMATE+|PAW3955Master|1,18|65285|1|noBottomButton,noDpiRGB",
    "373b|130e|ATK|ATK X1 SE NK|CORE26K|2,33|65285|1|isNearlink,noAthleticsMax,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|130f|ATK|ATK A9 SE 3.0 NK|PAW3315|2,32|65285|1|isNearlink,noAthleticsMax,noDpiRGB,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|1310|VXE|VXE R1 S NK|PAW3315|2,31|65285|1|isNearlink,noAthleticsMax,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|1311|VXE|VXE R1N NK|CORE26K|2,28|65285|1|isNearlink,noAthleticsMax,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|1312|ATK|ATK PHANTOM ULTIMATE|PAW3955Master|1,21|65285|1|noBottomButton,noDpiRGB",
    "373b|1314|ATK|ATK A9 Ultimate|PAW3950Ultra|1,22|65285|1|noDpiRGB",
    "373b|1319|VXE|VXE R1 SE+|CORE26K|2,22|65285|1|isNearlink,noAthleticsMax,noFarDistance,noSensorAngle,noXyDpi,noZeroKeyDebounce",
    "373b|131a|ATK|ATK X1 S NK|PAW3950Ultra|2,132|65284|2|noFarDistance,noZeroKeyDebounce",
    "373b|132b|ATK|MAD R|PAW3950Ultra|2,133|65284|2|noBottomButton",
    "373b|1337|ATK|ATK A9 Plus 3.0 NK|PAW3950Ultra|2,34|65285|1|isNearlink,noDpiRGB,noFarDistance",
    "373b|1338|ATK|ATK A9 Pro Max 3.0 NK|PAW3950Ultra|2,35|65285|1|isNearlink,noDpiRGB,noFarDistance",
)


def _parse_rows() -> tuple[Model, ...]:
    models: list[Model] = []
    for row in _RAW_MODELS:
        vendor, product, brand, name, sensor, cid, page, usage, feats = row.split("|")
        models.append(
            Model(
                vendor=int(vendor, 16),
                product=int(product, 16),
                brand=brand,
                name=name,
                sensor=sensor,
                cid_mid=None if cid == "-" else cid,
                usage_page=int(page),
                usage=int(usage),
                features=frozenset() if feats == "-" else frozenset(feats.split(",")),
            )
        )
    return tuple(models)


MODELS: tuple[Model, ...] = _parse_rows()

_BY_PID: dict[tuple[int, int], list[Model]] = {}
_BY_CID_MID: dict[str, list[Model]] = {}
for _model in MODELS:
    _BY_PID.setdefault((_model.vendor, _model.product), []).append(_model)
    if _model.cid_mid:
        _BY_CID_MID.setdefault(_model.cid_mid, []).append(_model)


def models_for_pid(vendor: int, product: int) -> list[Model]:
    return list(_BY_PID.get((vendor, product), []))


def models_for_cid_mid(cid_mid: str) -> list[Model]:
    return list(_BY_CID_MID.get(cid_mid, []))


def model_for_cid_mid(cid_mid: str, vendor: int | None = None) -> Model | None:
    """Eindeutige Identifikation: CID/MID, bei Bedarf per VID/PID eingegrenzt."""
    candidates = models_for_cid_mid(cid_mid)
    if not candidates:
        return None
    if vendor is not None:
        scoped = [m for m in candidates if m.vendor == vendor]
        if scoped:
            candidates = scoped
    if len(candidates) == 1:
        return candidates[0]
    # Mehrere Modelle teilen CID/MID: zuerst ohne Dongle-PID (Kabel-/Fest-PID)
    specific = [m for m in candidates if not m.is_receiver]
    if len(specific) == 1:
        return specific[0]
    return candidates[0]


def generic_model(vendor: int, product: int, name: str = "") -> Model:
    """Fallback, wenn CID/MID unbekannt ist (Dongle-PID oder unbekanntes Modell)."""
    label = RECEIVERS.get(product, name or f"Unbekanntes Modell {product:04x}")
    return Model(
        vendor=vendor,
        product=product,
        brand="ATK" if vendor == 0x373B else "VXE",
        name=label,
        sensor="unknown",
        cid_mid=None,
        usage_page=0xFF02,
        usage=0x0002,
        features=frozenset(),
    )


def find_model(
    vendor: int,
    product: int,
    cid_mid: str | None = None,
    product_name: str = "",
) -> Model:
    if cid_mid:
        model = model_for_cid_mid(cid_mid, vendor)
        if model is not None:
            return model
    candidates = models_for_pid(vendor, product)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        named = [m for m in candidates if product_name and m.name.lower() in product_name.lower()]
        if named:
            return named[0]
    return generic_model(vendor, product, product_name)
