from django.db import models

class OrdenFabricacion(models.Model):
    """
    Modelo que representa una Orden de Fabricación (OF) en el sistema.
    Almacena tanto los datos importados desde el Excel de Producción (PLANI)
    como los parámetros técnicos y de control interno registrados en planta.
    """

    # ============================================================
    # 1. IDENTIFICACIÓN Y DATOS GENERALES (Desde Excel)
    # ============================================================
    of = models.CharField(
        max_length=20, 
        verbose_name="Número de OF",
        help_text="Número identificador único de la orden de fabricación."
    )
    referencia = models.CharField(
        max_length=20, 
        blank=True, 
        null=True, 
        verbose_name="OF Referencia",
        help_text="Número de OF de referencia si aplica."
    )
    descripcion = models.TextField(
        verbose_name="Descripción del trabajo",
        help_text="Detalle del producto o trabajo a realizar."
    )
    cliente = models.CharField(
        max_length=150,
        verbose_name="Cliente",
        help_text="Nombre del cliente propietario de la orden."
    )
    tipo_grabado = models.CharField(
        max_length=50, 
        blank=True, 
        null=True, 
        verbose_name="Tipo de Grabado",
        help_text="Especificación del tipo de grabado solicitado."
    )
    proceso = models.CharField(
        max_length=50,
        verbose_name="Proceso",
        help_text="Departamento: STAMPING o EMBOSSING."
    )
    maquina = models.CharField(
        max_length=100, 
        blank=True, 
        null=True, 
        verbose_name="Máquina",
        help_text="Máquina asignada para el proceso."
    )
    fecha_programada = models.DateField(
        null=True, 
        blank=True, 
        verbose_name="Fecha Prog.",
        help_text="Fecha de producción establecida en la planificación."
    )
    fecha_registro = models.DateField(
        null=True, 
        blank=True, 
        verbose_name="Fecha de Guardado",
        help_text="Fecha en la que el usuario guardó el registro en EIS."
    )
    usuario = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Usuario de Registro",
        help_text="Usuario del sistema que realizó la última acción sobre este registro."
    )

    # ============================================================
    # 2. LOGÍSTICA Y MATERIALES (Sincronizados)
    # ============================================================
    cantidad_formatos = models.IntegerField(
        null=True, 
        blank=True, 
        verbose_name="Cantidad Formatos",
        help_text="Volumen total de formatos a producir."
    )
    horas_proceso = models.FloatField(
        null=True, 
        blank=True, 
        verbose_name="Horas Proceso",
        help_text="Tiempo estimado de producción en horas."
    )
    papel = models.CharField(
        max_length=100, 
        blank=True, 
        null=True, 
        verbose_name="Papel",
        help_text="Tipo de papel a utilizar."
    )

    # ============================================================
    # 3. CONTROL INTERNO (Gestión en Planta)
    # ============================================================
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente'),
        ('EN_PROCESO', 'En Proceso'),
        ('EN_MAQUINA', 'En Máquina'),
        ('COMPLETADO', 'Completado'),
        ('REVISION', 'En Revisión'),
        ('CANCELADO', 'Cancelado'),
        ('REPETIR', 'Para Repetir'),
    ]
    estado = models.CharField(
        max_length=20, 
        choices=ESTADO_CHOICES, 
        default='PENDIENTE',
        verbose_name="Estado Actual"
    )
    ubicacion = models.CharField(
        max_length=200, 
        blank=True, 
        null=True, 
        verbose_name="Ubicación Física",
        help_text="Lugar donde se encuentra el grabado físico (ej. Cajón A1)."
    )
    sobre = models.CharField(
        max_length=100, 
        blank=True, 
        null=True, 
        verbose_name="Número de Sobre/Caja",
        help_text="Identificador del sobre o caja de arte."
    )
    
    # ============================================================
    # 4. PARÁMETROS TÉCNICOS E INDUSTRIALES
    # ============================================================
    responsables = models.CharField(
        max_length=255, 
        null=True, 
        blank=True, 
        verbose_name="Responsables",
        help_text="Nombres de los operarios que trabajaron en la orden."
    )
    peso_inicial = models.FloatField(
        null=True, 
        blank=True, 
        verbose_name="Peso Inicial",
        help_text="Peso del material al iniciar el proceso."
    )
    peso_final = models.FloatField(
        null=True, 
        blank=True, 
        verbose_name="Peso Final",
        help_text="Peso del material al finalizar el proceso."
    )
    perdida = models.FloatField(
        null=True, 
        blank=True, 
        verbose_name="Pérdida",
        help_text="Diferencia de peso calculada durante el proceso."
    )
    temp = models.FloatField(
        null=True, 
        blank=True, 
        verbose_name="Temperatura",
        help_text="Temperatura de trabajo en grados centígrados."
    )
    rpm = models.IntegerField(
        null=True, 
        blank=True, 
        verbose_name="RPM",
        help_text="Revoluciones por minuto de la maquinaria."
    )
    tiempo = models.CharField(
        max_length=50, 
        null=True, 
        blank=True, 
        verbose_name="Tiempo",
        help_text="Duración real del proceso técnico."
    )
    compensacion = models.CharField(
        max_length=100, 
        null=True, 
        blank=True, 
        verbose_name="Compensación",
        help_text="Ajustes técnicos de compensación realizados."
    )
    usos_acumulados = models.IntegerField(
        default=0,
        verbose_name="Usos Acumulados",
        help_text="Contador de veces que este grabado ha pasado por producción."
    )
    foto_dano = models.ImageField( 
        upload_to='grabados/danos/%Y/%m/',
        null=True,
        blank=True,
        verbose_name="Foto de Daño",
        help_text="Evidencia visual en caso de reporte de daño o repetición."
    )

    # ============================================================
    # 5. METADATOS Y AUDITORÍA
    # ============================================================
    actualizado_el = models.DateTimeField(auto_now=True, verbose_name="Última Actualización")
    creado_el = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Creación")

    def __str__(self):
        return f"OF {self.of} - {self.cliente}"

    class Meta:
        verbose_name = "Orden de Fabricación"
        verbose_name_plural = "Órdenes de Fabricación"
        ordering = ['-fecha_programada']
        # Regla de integridad: Una misma OF puede estar en diferentes procesos,
        # pero no se puede repetir la misma OF dentro del mismo proceso.
        unique_together = [['of', 'proceso']]
