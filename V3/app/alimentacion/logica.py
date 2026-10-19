"""Reglas de Alimentación (macros de lo que se come y objetivos diarios),
sin base de datos ni Flask."""

from dataclasses import dataclass, field
from datetime import date

COMIDAS = {
    "desayuno": "Desayuno",
    "almuerzo": "Almuerzo",
    "merienda": "Merienda",
    "cena": "Cena",
    "colacion": "Colación",
}

# Factor de actividad que multiplica el metabolismo basal.
ACTIVIDADES = {
    "sedentario": (1.2, "Sedentario", "Poco o nada de ejercicio"),
    "ligero": (1.375, "Ligero", "Ejercicio 1 a 3 días por semana"),
    "moderado": (1.55, "Moderado", "Ejercicio 3 a 5 días por semana"),
    "alto": (1.725, "Alto", "Ejercicio 6 a 7 días por semana"),
    "muy_alto": (1.9, "Muy alto", "Doble turno o trabajo físico pesado"),
}

# Ajuste de calorías sobre el mantenimiento y proteínas en g por kg de peso.
OBJETIVOS = {
    "definir": (-0.20, 2.2, "Definir", "Bajar grasa: 20 % menos calorías"),
    "mantener": (0.0, 1.8, "Mantener", "Quedarte en tu peso"),
    "volumen": (0.10, 2.0, "Volumen", "Ganar músculo: 10 % más calorías"),
}
GRASAS_G_KG = 0.9

# Valores aproximados cada 100 g (carbohidratos totales). Se cargan una vez
# al crear la sección y hay un botón para volver a sumar los que falten.
# (nombre, kcal, proteínas, carbohidratos, grasas, gramos por unidad, unidad)
ALIMENTOS_BASICOS = (
    ("Pechuga de pollo (cruda)", 120, 22.5, 0, 2.6, None, ""),
    ("Pechuga de pollo (cocida)", 165, 31, 0, 3.6, None, ""),
    ("Huevo", 143, 12.6, 0.7, 9.5, 50, "huevo"),
    ("Repollo", 25, 1.3, 5.8, 0.1, None, ""),
    ("Papa (cruda)", 77, 2, 17.5, 0.1, 170, "papa"),
    ("Milanesa de soja", 220, 12, 24, 8.5, 80, "milanesa"),
    ("Zanahoria", 41, 0.9, 9.6, 0.2, 60, "zanahoria"),
    ("Yogur casero", 61, 3.5, 4.7, 3.3, None, ""),
    ("Leche proteica", 55, 6, 5, 1.5, 200, "vaso"),
    ("Batata (cruda)", 86, 1.6, 20.1, 0.1, 200, "batata"),
    ("Boniato (crudo)", 86, 1.6, 20.1, 0.1, 200, "boniato"),
)


@dataclass(frozen=True)
class Macros:
    kcal: float = 0.0
    proteinas: float = 0.0
    carbohidratos: float = 0.0
    grasas: float = 0.0

    def __add__(self, otro: "Macros") -> "Macros":
        return Macros(self.kcal + otro.kcal, self.proteinas + otro.proteinas,
                      self.carbohidratos + otro.carbohidratos, self.grasas + otro.grasas)

    def escalar(self, factor: float) -> "Macros":
        return Macros(self.kcal * factor, self.proteinas * factor, self.carbohidratos * factor, self.grasas * factor)

    def redondear(self) -> "Macros":
        return Macros(round(self.kcal, 1), round(self.proteinas, 1), round(self.carbohidratos, 1), round(self.grasas, 1))


def sumar(macros) -> Macros:
    total = Macros()
    for m in macros:
        total += m
    return total


@dataclass(frozen=True)
class Alimento:
    id: int
    nombre: str
    por_100g: Macros
    gramos_unidad: float | None = None
    unidad: str = ""

    def macros(self, gramos: float) -> Macros:
        return self.por_100g.escalar(gramos / 100).redondear()


@dataclass(frozen=True)
class Entrada:
    """Algo que se comió. Guarda sus macros calculadas al anotarlo, así
    editar el alimento después no cambia lo ya registrado. `unidad` es "g",
    el nombre de la unidad del alimento (ej. "huevo") o "" si se cargaron
    las macros a mano."""

    id: int
    fecha: date
    comida: str
    nombre: str
    macros: Macros
    alimento_id: int | None = None
    cantidad: float | None = None
    unidad: str = ""
    gramos: float | None = None

    @property
    def manual(self) -> bool:
        return not self.unidad


@dataclass(frozen=True)
class Frecuente:
    """Una comida guardada para cargarla entera con un clic."""

    id: int
    nombre: str
    comida: str
    items: tuple[Entrada, ...] = field(default_factory=tuple)

    @property
    def macros(self) -> Macros:
        return sumar(i.macros for i in self.items)


def gramos_de(alimento: Alimento, cantidad: float, por_unidad: bool) -> float:
    if por_unidad:
        if not alimento.gramos_unidad:
            raise ValueError(f"\"{alimento.nombre}\" no tiene una unidad definida: cargalo en gramos.")
        return cantidad * alimento.gramos_unidad
    return cantidad


def edad(nacimiento: date, hoy: date) -> int:
    return hoy.year - nacimiento.year - ((hoy.month, hoy.day) < (nacimiento.month, nacimiento.day))


def metabolismo_basal(sexo: str, peso: float, altura_cm: float, anios: int) -> float:
    """Calorías que el cuerpo gasta en reposo (fórmula de Mifflin-St Jeor)."""
    return 10 * peso + 6.25 * altura_cm - 5 * anios + (5 if sexo == "h" else -161)


def calcular_objetivos(sexo: str, peso: float, altura_cm: float, anios: int, actividad: str, objetivo: str) -> dict:
    """Calorías y macros diarias sugeridas: proteínas y grasas según el
    peso, y los carbohidratos completan las calorías."""
    basal = metabolismo_basal(sexo, peso, altura_cm, anios)
    mantenimiento = basal * ACTIVIDADES[actividad][0]
    ajuste, proteinas_g_kg = OBJETIVOS[objetivo][:2]
    kcal = round(mantenimiento * (1 + ajuste) / 10) * 10
    proteinas = round(peso * proteinas_g_kg)
    grasas = round(peso * GRASAS_G_KG)
    carbohidratos = max(0, round((kcal - proteinas * 4 - grasas * 9) / 4))
    return {
        "basal": round(basal),
        "mantenimiento": round(mantenimiento),
        "kcal": kcal,
        "proteinas": proteinas,
        "carbohidratos": carbohidratos,
        "grasas": grasas,
    }


def porcentaje(valor: float, meta: float | None) -> int | None:
    return round(valor * 100 / meta) if meta else None
