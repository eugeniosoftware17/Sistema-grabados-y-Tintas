from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

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
        verbose_name="Peso Inicial (g)",
        help_text="Peso del material al iniciar el proceso, en gramos."
    )
    peso_final = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Peso Final (g)",
        help_text="Peso del material al finalizar el proceso, en gramos."
    )
    perdida = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Pérdida (g)",
        help_text="Diferencia de peso calculada durante el proceso, en gramos."
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
        verbose_name="Tiempo Real (min)",
        help_text="Duración real del proceso técnico, en minutos."
    )
    compensacion = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name="Compensación (ml)",
        help_text="Cantidad de compensación de baño aplicada, en ml."
    )
    compensacion_motivo = models.TextField(
        null=True,
        blank=True,
        verbose_name="Motivo del ajuste de compensación",
        help_text="Explicación de por qué se cambió la compensación respecto al valor "
                   "recomendado (Pérdida × 6.6)."
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
    origen_manual = models.BooleanField(
        default=False,
        verbose_name="Alta manual (Fabricación)",
        help_text="Se marca solo cuando el grabado se registró desde la pantalla temporal de "
                   "Fabricación, en vez de venir de la programación de Planning."
    )

    def __str__(self):
        return f"OF {self.of} - {self.cliente}"

    class Meta:
        verbose_name = "Orden de Fabricación"
        verbose_name_plural = "Órdenes de Fabricación"
        ordering = ['-fecha_programada']
        # Regla de integridad: Una misma OF puede estar en diferentes procesos,
        # pero no se puede repetir la misma OF dentro del mismo proceso.
        unique_together = [['of', 'proceso']]


class EstadoBano(models.Model):
    """
    Contador único (una sola fila) de la compensación (ml) acumulada en el
    baño físico compartido por todas las máquinas. Cada vez que se registra
    un grabado se le suma el "Baño sugerido" de esa corrida. Al llegar a
    LIMITE_ML hay que cambiar el agua del baño y reiniciar el contador.
    """
    LIMITE_ML = 2000

    ml_acumulados = models.FloatField(
        default=0,
        verbose_name="ML acumulados desde la última renovación"
    )
    actualizado_el = models.DateTimeField(auto_now=True)
    ultima_renovacion = models.DateTimeField(null=True, blank=True)
    renovado_por = models.CharField(max_length=150, null=True, blank=True)

    class Meta:
        verbose_name = "Estado del Baño"
        verbose_name_plural = "Estado del Baño"

    def __str__(self):
        return f"Baño: {self.ml_acumulados:.0f} / {self.LIMITE_ML} ml"

    @classmethod
    def obtener(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ============================================================
# REESTRUCTURACIÓN (fase 1): el grabado como entidad propia.
# OrdenFabricacion queda como histórico legado; ver LegadoOrden.
# ============================================================

PROCESO_CHOICES = [
    ('STAMPING', 'Stamping'),
    ('EMBOSSING', 'Embossing'),
]


class Maquina(models.Model):
    """
    Catálogo de máquinas (GIETZ 01, STAR FOIL, ...). Reemplaza el texto libre
    que hoy viene de la columna RESPONSABLE del Excel.
    """
    nombre = models.CharField(max_length=100, unique=True, verbose_name="Nombre")
    activa = models.BooleanField(default=True, verbose_name="Activa")

    class Meta:
        verbose_name = "Máquina"
        verbose_name_plural = "Máquinas"
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Grabado(models.Model):
    """
    Pieza física. Se identifica por la OF con la que se creó (la que las OF
    nuevas del PLANI traen como "OF Referencia") y su proceso.
    """
    ESTADO_CHOICES = [
        ('EN_FABRICACION', 'En Fabricación'),
        ('PENDIENTE_K1', 'Pendiente de K1'),
        ('APROBADO', 'Aprobado'),
        ('EN_MAQUINA', 'En Máquina'),
        ('REPETIR', 'Para Repetir'),
    ]

    of_origen = models.CharField(
        max_length=20,
        verbose_name="OF de origen",
        help_text="Solo dígitos, misma normalización que el PLANI."
    )
    proceso = models.CharField(max_length=20, choices=PROCESO_CHOICES, verbose_name="Proceso")

    # Copiados de la API (G_Cliente, G_Referencia, Sobre_pelicula) al dar de alta.
    cliente = models.CharField(max_length=150, verbose_name="Cliente")
    referencia = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Referencia",
        help_text="Descripción del producto (G_Referencia)."
    )
    sobre = models.CharField(max_length=100, blank=True, null=True, verbose_name="Sobre/Caja")
    datos_manuales = models.BooleanField(
        default=False,
        verbose_name="Datos cargados a mano",
        help_text="La OF de origen no se encontró en la API."
    )

    estado = models.CharField(
        max_length=20,
        choices=ESTADO_CHOICES,
        default='EN_FABRICACION',
        verbose_name="Estado"
    )
    aprobado_legado = models.BooleanField(
        default=False,
        verbose_name="Aprobado sin K1 (legado)",
        help_text="Migrado desde OrdenFabricacion; nunca pasó K1 en EIS."
    )
    ubicacion = models.CharField(max_length=200, blank=True, null=True, verbose_name="Ubicación física")
    usos_acumulados = models.PositiveIntegerField(default=0, verbose_name="Usos acumulados")

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+', verbose_name="Creado por"
    )
    creado_el = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Creación")
    actualizado_el = models.DateTimeField(auto_now=True, verbose_name="Última Actualización")

    class Meta:
        verbose_name = "Grabado"
        verbose_name_plural = "Grabados"
        ordering = ['-actualizado_el']
        constraints = [
            models.UniqueConstraint(fields=['of_origen', 'proceso'], name='grabado_unico_of_proceso'),
        ]
        indexes = [models.Index(fields=['estado'], name='grabado_estado_idx')]

    def __str__(self):
        return f"Grabado {self.of_origen} {self.proceso}"


class FabricacionGrabado(models.Model):
    """
    Historial: una fila por cada vez que el grabado se fabrica. Los datos
    técnicos viven solo acá; PruebaK1 apunta a la fabricación que prueba.
    """
    TIPO_CHOICES = [
        ('INICIAL', 'Fabricación inicial'),
        ('RECHAZO_K1', 'Refabricación por K1 rechazado'),
        ('REPETICION', 'Refabricación por REPETIR'),
    ]

    grabado = models.ForeignKey(Grabado, on_delete=models.PROTECT, related_name='fabricaciones')
    numero = models.PositiveIntegerField(verbose_name="N° de fabricación")
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, verbose_name="Tipo")

    # Mismos tipos que OrdenFabricacion para copiar sin pérdida en la migración.
    responsables = models.CharField(max_length=255, blank=True, null=True, verbose_name="Responsables")
    peso_inicial = models.FloatField(null=True, blank=True, verbose_name="Peso Inicial (g)")
    peso_final = models.FloatField(null=True, blank=True, verbose_name="Peso Final (g)")
    perdida = models.FloatField(null=True, blank=True, verbose_name="Pérdida (g)")
    temp = models.FloatField(null=True, blank=True, verbose_name="Temperatura")
    rpm = models.IntegerField(null=True, blank=True, verbose_name="RPM")
    tiempo = models.CharField(max_length=50, null=True, blank=True, verbose_name="Tiempo Real (min)")
    compensacion = models.CharField(max_length=100, null=True, blank=True, verbose_name="Compensación (ml)")
    compensacion_motivo = models.TextField(null=True, blank=True, verbose_name="Motivo del ajuste de compensación")
    bano_ml = models.FloatField(default=0, verbose_name="ML sumados al baño")

    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+', verbose_name="Registrado por"
    )
    registrado_el = models.DateTimeField(default=timezone.now, verbose_name="Fecha de registro")

    class Meta:
        verbose_name = "Fabricación de grabado"
        verbose_name_plural = "Fabricaciones de grabado"
        ordering = ['grabado', 'numero']
        constraints = [
            models.UniqueConstraint(fields=['grabado', 'numero'], name='fabricacion_numero_unico'),
        ]

    def __str__(self):
        return f"{self.grabado} - fabricación {self.numero} ({self.tipo})"


class PruebaK1(models.Model):
    """
    Prueba K1 de una fabricación INICIAL o RECHAZO_K1. Siempre con la OF de
    origen del grabado (no se guarda aparte). Un rechazo cierra este K1; el
    siguiente intento se abre al registrar la nueva fabricación.
    """
    RESULTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente'),
        ('APROBADO', 'Aprobado'),
        ('RECHAZADO', 'Rechazado'),
    ]

    grabado = models.ForeignKey(Grabado, on_delete=models.PROTECT, related_name='pruebas_k1')
    fabricacion = models.OneToOneField(
        FabricacionGrabado, on_delete=models.PROTECT, related_name='prueba_k1'
    )
    intento = models.PositiveIntegerField(verbose_name="Intento")
    maquina = models.ForeignKey(Maquina, on_delete=models.PROTECT, related_name='pruebas_k1',
                                verbose_name="Máquina")

    resultado = models.CharField(max_length=20, choices=RESULTADO_CHOICES, default='PENDIENTE',
                                 verbose_name="Resultado")
    motivo_rechazo = models.TextField(blank=True, null=True, verbose_name="Motivo de rechazo")
    decidido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+', verbose_name="Decidido por"
    )
    decidido_el = models.DateTimeField(null=True, blank=True, verbose_name="Fecha de decisión")

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+', verbose_name="Creado por"
    )
    creado_el = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Creación")

    class Meta:
        verbose_name = "Prueba K1"
        verbose_name_plural = "Pruebas K1"
        ordering = ['grabado', 'intento']
        permissions = [('decidir_pruebak1', 'Puede aprobar o rechazar pruebas K1')]
        constraints = [
            models.UniqueConstraint(fields=['grabado', 'intento'], name='k1_intento_unico'),
            # Índice filtrado: como mucho un K1 pendiente por grabado.
            models.UniqueConstraint(fields=['grabado'], condition=Q(resultado='PENDIENTE'),
                                    name='k1_un_pendiente_por_grabado'),
            models.CheckConstraint(condition=~Q(resultado='RECHAZADO') | Q(motivo_rechazo__isnull=False),
                                   name='k1_rechazo_con_motivo'),
        ]

    def __str__(self):
        return f"K1 {self.grabado} intento {self.intento} ({self.resultado})"


class EnvioMaquina(models.Model):
    """Uso del grabado para tirar una OF del PLANI: envío a máquina y recogida."""
    ESTADO_FISICO_CHOICES = [
        ('OK', 'OK'),
        ('REPETIR', 'Repetir'),
    ]

    grabado = models.ForeignKey(Grabado, on_delete=models.PROTECT, related_name='envios')
    of = models.CharField(max_length=20, verbose_name="OF del PLANI")
    maquina = models.ForeignKey(Maquina, on_delete=models.PROTECT, related_name='envios',
                                verbose_name="Máquina")

    # Foto de la fila del Excel al momento del envío.
    fecha_programada = models.DateField(null=True, blank=True, verbose_name="Fecha Prog.")
    cantidad_formatos = models.IntegerField(null=True, blank=True, verbose_name="Cantidad Formatos")
    horas_proceso = models.FloatField(null=True, blank=True, verbose_name="Horas Proceso")
    papel = models.CharField(max_length=100, blank=True, null=True, verbose_name="Papel")

    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+', verbose_name="Enviado por"
    )
    enviado_el = models.DateTimeField(verbose_name="Fecha de envío")

    recogido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+', verbose_name="Recogido por"
    )
    recogido_el = models.DateTimeField(null=True, blank=True, verbose_name="Fecha de recogida")
    estado_fisico = models.CharField(max_length=10, choices=ESTADO_FISICO_CHOICES,
                                     blank=True, null=True, verbose_name="Estado físico")
    comentario = models.TextField(blank=True, null=True, verbose_name="Comentario")
    foto_dano = models.ImageField(upload_to='grabados/danos/%Y/%m/', null=True, blank=True,
                                  verbose_name="Foto de Daño")
    ubicacion = models.CharField(max_length=200, blank=True, null=True,
                                 verbose_name="Ubicación al recoger")

    class Meta:
        verbose_name = "Envío a máquina"
        verbose_name_plural = "Envíos a máquina"
        ordering = ['-enviado_el']
        constraints = [
            # Índice filtrado: como mucho un envío abierto (sin recoger) por grabado.
            models.UniqueConstraint(fields=['grabado'], condition=Q(recogido_el__isnull=True),
                                    name='envio_uno_abierto_por_grabado'),
        ]
        indexes = [models.Index(fields=['of'], name='envio_of_idx')]

    def __str__(self):
        return f"OF {self.of} con {self.grabado}"


class LegadoOrden(models.Model):
    """
    Enlace de cada fila de OrdenFabricacion con lo que resultó de la migración.
    Es el 'legado_orden_id': OrdenFabricacion no se modifica y todas sus filas
    quedan registradas acá exactamente una vez (sirve de conciliación).
    """
    ROL_CHOICES = [
        ('FABRICACION', 'Convertida en FabricacionGrabado'),
        ('USO', 'Uso del grabado (solo histórico)'),
        ('ENVIO_ABIERTO', 'Convertida en EnvioMaquina abierto'),
        ('SIN_MIGRAR', 'Sin migrar (ej. PENDIENTE)'),
    ]

    orden = models.OneToOneField(OrdenFabricacion, on_delete=models.PROTECT, related_name='legado')
    rol = models.CharField(max_length=20, choices=ROL_CHOICES, verbose_name="Rol")
    grabado = models.ForeignKey(Grabado, on_delete=models.PROTECT, null=True, blank=True,
                                related_name='ordenes_legado')
    fabricacion = models.OneToOneField(FabricacionGrabado, on_delete=models.PROTECT,
                                       null=True, blank=True, related_name='orden_legado')
    envio = models.OneToOneField(EnvioMaquina, on_delete=models.PROTECT,
                                 null=True, blank=True, related_name='orden_legado')
    migrado_el = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de migración")

    class Meta:
        verbose_name = "Enlace con orden legada"
        verbose_name_plural = "Enlaces con órdenes legadas"

    def __str__(self):
        return f"Orden legada {self.orden_id} -> {self.rol}"
