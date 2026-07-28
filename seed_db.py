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

def seed_data(n=10000):
    print(f"Limpiando datos antiguos de prueba (Rango 17000-23000)...")
    # Borramos solo el rango de prueba para no afectar datos reales si existieran
    OrdenFabricacion.objects.filter(of__gte='17000', of__lte='23000').delete()
    
    print(f"Iniciando la creación de {n} registros...")
    
    # Obtener el usuario admin
    user = User.objects.filter(username='admin').first()
    if not user:
        user = User.objects.create_superuser('admin', 'admin@example.com', 'admin123')
        print("Usuario 'admin' creado.")

    clientes = ["Arturo Fuente", "Davidoff", "Padrón", "Montecristo", "Cohiba", 
                "Romeo y Julieta", "Rocky Patel", "Perdomo", "La Flor Dominicana", "My Father Cigars"]
    
    procesos = ['STAMPING', 'EMBOSSING']
    estados = ['COMPLETADO', 'EN_PROCESO', 'PENDIENTE', 'REVISION', 'REPETIR']
    papeles = ["Metalizado Oro", "Metalizado Plata", "Mate Blanco", "Texturizado Crema", "Brillante Negro"]
    
    # Generamos todas las combinaciones posibles en el rango para evitar errores de unicidad
    # Rango 17000 a 23000 = 6001 números. x 2 procesos = 12,002 combinaciones.
    all_ofs = [str(x) for x in range(17000, 23001)]
    all_combinations = []
    for of_num in all_ofs:
        for proc in procesos:
            all_combinations.append((of_num, proc))
    
    # Mezclamos las combinaciones y tomamos las primeras N
    random.shuffle(all_combinations)
    selected = all_combinations[:n]
    
    objs = []
    start_date = date(2021, 1, 1)
    
    for i in range(len(selected)):
        of_val, proceso_val = selected[i]
        
        # Referencia aleatoria a otra OF del pool
        ref_val = random.choice(all_ofs)
        if ref_val == of_val: ref_val = None

        days_offset = random.randint(0, 365 * 5 + 150)
        fecha_prog = start_date + timedelta(days=days_offset)
        
        temp_val = random.uniform(120, 190)
        peso_ini = random.uniform(5.0, 50.0)
        perdida_val = random.uniform(0.1, 1.5)

        objs.append(OrdenFabricacion(
            of=of_val,
            referencia=ref_val,
            descripcion=f"Producción {random.choice(clientes)} - {fecha_prog.year}",
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
            responsables="Op. " + random.choice(["García", "Rodríguez", "Martínez"]),
            peso_inicial=round(peso_ini, 2),
            peso_final=round(peso_ini - perdida_val, 2),
            perdida=round(perdida_val, 2),
            temp=round(temp_val, 1),
            rpm=random.randint(500, 3000),
            tiempo=f"{random.randint(1, 10)}h",
            compensacion=f"{random.uniform(0, 0.8):.2f}mm",
            usos_acumulados=random.randint(0, 50)
        ))

    # Inserción masiva en lotes de 500
    OrdenFabricacion.objects.bulk_create(objs, batch_size=500)
    print(f"¡Éxito! Se han creado {len(objs)} registros en la base de datos.")

if __name__ == "__main__":
    seed_data(10000)
