import os
import django
import random
import string
from datetime import date, timedelta

# Configuración del entorno de Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.gestion_grabados.models import OrdenFabricacion
from django.contrib.auth.models import User

def generate_location():
    """Genera una ubicación con el formato AA-B1"""
    letters = "".join(random.choices(string.ascii_uppercase, k=2))
    shelf = random.choice(string.ascii_uppercase)
    level = random.randint(1, 9)
    return f"{letters}-{shelf}{level}"

def add_more_data(n=1000):
    print(f"Buscando combinaciones disponibles...")
    
    # 1. Obtener combinaciones ya existentes para no repetirlas
    existing = set(OrdenFabricacion.objects.filter(
        of__gte='17000', of__lte='23000'
    ).values_list('of', 'proceso'))
    
    print(f"Ya existen {len(existing)} registros.")

    # 2. Definir el universo total posible
    procesos = ['STAMPING', 'EMBOSSING']
    all_ofs = [str(x) for x in range(17000, 23001)]
    
    available_combinations = []
    for of_num in all_ofs:
        for proc in procesos:
            if (of_num, proc) not in existing:
                available_combinations.append((of_num, proc))
    
    print(f"Espacios disponibles encontrados: {len(available_combinations)}")

    if len(available_combinations) < n:
        print(f"Advertencia: Solo quedan {len(available_combinations)} espacios. Ajustando a ese máximo.")
        n = len(available_combinations)

    # 3. Mezclar y seleccionar N
    random.shuffle(available_combinations)
    selected = available_combinations[:n]

    # 4. Preparar datos
    user = User.objects.filter(username='admin').first()
    clientes = ["Arturo Fuente", "Davidoff", "Padrón", "Montecristo", "Cohiba", 
                "Romeo y Julieta", "Rocky Patel", "Perdomo", "La Flor Dominicana", "My Father Cigars"]
    estados = ['COMPLETADO', 'EN_PROCESO', 'PENDIENTE', 'REVISION', 'REPETIR']
    papeles = ["Metalizado Oro", "Metalizado Plata", "Mate Blanco", "Texturizado Crema", "Brillante Negro"]
    
    objs = []
    start_date = date(2021, 1, 1)
    
    for i in range(len(selected)):
        of_val, proceso_val = selected[i]
        ref_val = random.choice(all_ofs)
        if ref_val == of_val: ref_val = None

        days_offset = random.randint(0, 365 * 5 + 150)
        fecha_prog = start_date + timedelta(days=days_offset)
        
        temp_val = random.uniform(120, 190)
        peso_ini = random.uniform(5.0, 50.0)

        objs.append(OrdenFabricacion(
            of=of_val,
            referencia=ref_val,
            descripcion=f"Adicional {random.choice(clientes)} - {fecha_prog.year}",
            cliente=random.choice(clientes),
            tipo_grabado=f"G-{random.randint(100, 999)}",
            proceso=proceso_val,
            maquina=f"Máquina {random.randint(1, 10)}",
            fecha_programada=fecha_prog,
            fecha_registro=fecha_prog + timedelta(days=random.randint(0, 5)),
            usuario=user,
            cantidad_formatos=random.randint(1000, 100000),
            horas_proceso=round(random.uniform(0.5, 12.0), 1),
            papel=random.choice(papeles),
            estado=random.choices(estados, weights=[75, 10, 5, 5, 5])[0],
            ubicacion=generate_location(),
            sobre=f"Box-{random.randint(100, 999)}",
            responsables="Op. Adicional",
            peso_inicial=round(peso_ini, 2),
            peso_final=round(peso_ini - 0.5, 2),
            perdida=0.5,
            temp=round(temp_val, 1),
            rpm=random.randint(500, 3000),
            tiempo="4h",
            compensacion="0.20mm",
            usos_acumulados=random.randint(0, 50)
        ))

    # 5. Insertar
    OrdenFabricacion.objects.bulk_create(objs, batch_size=500)
    print(f"¡Éxito! Se han añadido {len(objs)} registros nuevos sin duplicar los anteriores.")

if __name__ == "__main__":
    add_more_data(1000)
