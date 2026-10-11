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
        # Un grabado nunca se elimina: se desactiva (services.desactivar_grabado).
        ('INACTIVO', 'Inactivo'),
    ]
    TIPO_CHOICES = [
        ('K1', 'K1 – de prueba'),
        ('DIRECTO', 'Producción – directo'),
        ('LEGADO', 'Legado (migrado)'),
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
    # Texto del sobre tal como vino de la API (se retira en un paso posterior;
    # el sobre físico es sobre_asociado).
    sobre = models.CharField(max_length=100, blank=True, null=True, verbose_name="Sobre/Caja")
    sobre_asociado = models.OneToOneField(
        'Sobre', on_delete=models.PROTECT, null=True, blank=True, related_name='grabado',
        verbose_name="Sobre", help_text="El sobre con los diseños de creación de este grabado."
    )
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
    tipo = models.CharField(
        max_length=10,
        choices=TIPO_CHOICES,
        # Sin default: quien crea el grabado tiene que decir el tipo (lo exige
        # además la restricción grabado_tipo_valido).
        verbose_name="Tipo",
        help_text="Cómo se creó: K1 (prueba, requiere aprobación), Producción directa "
                  "(aprobado al guardar, sin K1) o Legado (migrado desde OrdenFabricacion)."
    )
    ubicacion = models.CharField(max_length=200, blank=True, null=True, verbose_name="Ubicación física")
    usos_acumulados = models.PositiveIntegerField(default=0, verbose_name="Usos acumulados")

    # Última desactivación (se limpian al reactivar; el rastro completo queda en
    # HistorialActivacion). estado_previo es el estado al que vuelve al reactivar.
    desactivado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+', verbose_name="Desactivado por"
    )
    desactivado_el = models.DateTimeField(null=True, blank=True, verbose_name="Desactivado el")
    motivo_desactivacion = models.TextField(blank=True, default='', verbose_name="Motivo de desactivación")
    estado_previo = models.CharField(max_length=20, blank=True, default='', choices=ESTADO_CHOICES,
                                     verbose_name="Estado previo a la desactivación")

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
            models.CheckConstraint(condition=models.Q(tipo__in=['K1', 'DIRECTO', 'LEGADO']),
                                   name='grabado_tipo_valido'),
        ]
        indexes = [
            models.Index(fields=['estado'], name='grabado_estado_idx'),
            # El Inventario ordena por última modificación.
            models.Index(fields=['actualizado_el'], name='grabado_actualizado_idx'),
        ]

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
    revisar_pesos = models.BooleanField(
        default=False,
        verbose_name="Revisar pesos",
        help_text="Migrada de una fila anterior al 2026-08-12: los pesos se copiaron tal cual "
                  "y pueden estar en kg en vez de gramos."
    )

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

    # Copia de los datos de la fila del Excel al momento del envío.
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


class HistorialUbicacion(models.Model):
    """
    Una fila por cada cambio de ubicación de un grabado. Solo la escribe
    services.grabados.cambiar_ubicacion (edición en el Inventario o en la Tabla
    de Consultas, o recogida desde el PLANI).
    """
    ORIGEN_CHOICES = [
        ('INVENTARIO', 'Edición en el Inventario'),
        ('CONSULTA', 'Edición en la Tabla de Consultas'),
        ('RECOGIDA', 'Recogida desde el PLANI'),
    ]

    grabado = models.ForeignKey(Grabado, on_delete=models.PROTECT, related_name='historial_ubicaciones')
    ubicacion_anterior = models.CharField(max_length=200, blank=True, null=True,
                                          verbose_name="Ubicación anterior")
    ubicacion_nueva = models.CharField(max_length=200, verbose_name="Ubicación nueva")
    origen = models.CharField(max_length=20, choices=ORIGEN_CHOICES, verbose_name="Origen")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+', verbose_name="Usuario"
    )
    cambiado_el = models.DateTimeField(auto_now_add=True, verbose_name="Fecha del cambio")

    class Meta:
        verbose_name = "Cambio de ubicación"
        verbose_name_plural = "Historial de ubicaciones"
        ordering = ['-cambiado_el', '-id']

    def __str__(self):
        return f"{self.grabado}: {self.ubicacion_anterior or '—'} -> {self.ubicacion_nueva}"


class HistorialActivacion(models.Model):
    """
    Una fila por cada desactivación o reactivación de un grabado. Solo la
    escriben services.grabados.desactivar_grabado / reactivar_grabado.
    """
    ACCION_CHOICES = [
        ('DESACTIVAR', 'Desactivado'),
        ('REACTIVAR', 'Reactivado'),
    ]

    grabado = models.ForeignKey(Grabado, on_delete=models.PROTECT, related_name='historial_activaciones')
    accion = models.CharField(max_length=20, choices=ACCION_CHOICES, verbose_name="Acción")
    motivo = models.TextField(blank=True, default='', verbose_name="Motivo")
    # Al desactivar: el estado que tenía. Al reactivar: el estado al que volvió.
    estado = models.CharField(max_length=20, choices=Grabado.ESTADO_CHOICES, verbose_name="Estado")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+', verbose_name="Usuario"
    )
    fecha = models.DateTimeField(auto_now_add=True, verbose_name="Fecha")

    class Meta:
        verbose_name = "Activación / desactivación"
        verbose_name_plural = "Historial de activaciones"
        ordering = ['-fecha', '-id']

    def __str__(self):
        return f"{self.grabado}: {self.get_accion_display()}"


class Sobre(models.Model):
    """
    Sobre físico con los diseños de creación de un grabado: uno por grabado,
    con la misma OF de origen y proceso. Nunca se elimina: se desactiva
    (services.sobres.desactivar_sobre). Su estado no depende del del grabado.
    """
    ESTADO_CHOICES = [
        ('ACTIVO', 'Activo'),
        ('INACTIVO', 'Inactivo'),
    ]

    of_origen = models.CharField(max_length=20, verbose_name="OF de origen")
    proceso = models.CharField(max_length=20, choices=PROCESO_CHOICES, verbose_name="Proceso")
    codigo = models.CharField(
        max_length=100, blank=True, default='', verbose_name="Código",
        help_text="Número del sobre, limpio (10567.0 -> 10567). Puede quedar vacío."
    )
    ubicacion = models.CharField(max_length=200, blank=True, null=True, verbose_name="Ubicación (estantería)")
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default='ACTIVO', verbose_name="Estado")

    # Última desactivación (se limpian al reactivar; el rastro queda en HistorialActivacionSobre).
    desactivado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+', verbose_name="Desactivado por"
    )
    desactivado_el = models.DateTimeField(null=True, blank=True, verbose_name="Desactivado el")
    motivo_desactivacion = models.TextField(blank=True, default='', verbose_name="Motivo de desactivación")

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+', verbose_name="Creado por"
    )
    creado_el = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Creación")
    actualizado_el = models.DateTimeField(auto_now=True, verbose_name="Última Actualización")

    class Meta:
        verbose_name = "Sobre"
        verbose_name_plural = "Sobres"
        ordering = ['-actualizado_el']
        constraints = [
            models.UniqueConstraint(fields=['of_origen', 'proceso'], name='sobre_unico_of_proceso'),
            models.CheckConstraint(condition=models.Q(estado__in=['ACTIVO', 'INACTIVO']),
                                   name='sobre_estado_valido'),
        ]
        indexes = [
            # La pantalla Sobres ordena por última modificación.
            models.Index(fields=['actualizado_el'], name='sobre_actualizado_idx'),
        ]

    def __str__(self):
        return f"Sobre {self.codigo or '(sin código)'} – {self.of_origen} {self.proceso}"


class HistorialUbicacionSobre(models.Model):
    """
    Una fila por cada cambio de ubicación de un sobre. Solo la escribe
    services.sobres.cambiar_ubicacion_sobre.
    """
    ORIGEN_CHOICES = [
        ('SOBRES', 'Edición en la pantalla Sobres'),
        ('ALTA', 'Crear Grabado'),
    ]

    sobre = models.ForeignKey(Sobre, on_delete=models.PROTECT, related_name='historial_ubicaciones')
    ubicacion_anterior = models.CharField(max_length=200, blank=True, null=True,
                                          verbose_name="Ubicación anterior")
    ubicacion_nueva = models.CharField(max_length=200, verbose_name="Ubicación nueva")
    origen = models.CharField(max_length=20, choices=ORIGEN_CHOICES, verbose_name="Origen")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+', verbose_name="Usuario"
    )
    cambiado_el = models.DateTimeField(auto_now_add=True, verbose_name="Fecha del cambio")

    class Meta:
        verbose_name = "Cambio de ubicación de sobre"
        verbose_name_plural = "Historial de ubicaciones de sobres"
        ordering = ['-cambiado_el', '-id']

    def __str__(self):
        return f"{self.sobre}: {self.ubicacion_anterior or '—'} -> {self.ubicacion_nueva}"


class HistorialActivacionSobre(models.Model):
    """
    Una fila por cada desactivación o reactivación de un sobre. Solo la
    escriben services.sobres.desactivar_sobre / reactivar_sobre.
    """
    ACCION_CHOICES = HistorialActivacion.ACCION_CHOICES

    sobre = models.ForeignKey(Sobre, on_delete=models.PROTECT, related_name='historial_activaciones')
    accion = models.CharField(max_length=20, choices=ACCION_CHOICES, verbose_name="Acción")
    motivo = models.TextField(blank=True, default='', verbose_name="Motivo")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+', verbose_name="Usuario"
    )
    fecha = models.DateTimeField(auto_now_add=True, verbose_name="Fecha")

    class Meta:
        verbose_name = "Activación / desactivación de sobre"
        verbose_name_plural = "Historial de activaciones de sobres"
        ordering = ['-fecha', '-id']

    def __str__(self):
        return f"{self.sobre}: {self.get_accion_display()}"


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


class ConfiguracionEtiqueta(models.Model):
    """
    Diseño de la etiqueta 6x4" (una sola fila, pk=1) que usan todas las etiquetas
    de grabados y sobres. Se edita en el admin (solo superusuario) y se guarda por
    services.etiquetas.guardar_configuracion. Si la fila no existe se usan los
    valores por defecto (services.etiquetas.CAMPOS_POR_DEFECTO).
    """
    # Por cada campo de la etiqueta: visible, x, y, ancho, alto (pulgadas) y tamano (pt).
    campos = models.JSONField(default=dict, blank=True, verbose_name="Campos")
    # El logo va en la base (no hay MEDIA_ROOT); vacío = logo de static/img/logo/.
    logo = models.BinaryField(null=True, blank=True, verbose_name="Logo")
    logo_tipo = models.CharField(max_length=20, blank=True, default='', verbose_name="Tipo de imagen del logo")
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+', verbose_name="Actualizado por"
    )
    actualizado_el = models.DateTimeField(auto_now=True, verbose_name="Última Actualización")

    class Meta:
        verbose_name = "Diseño de etiqueta"
        verbose_name_plural = "Diseño de etiqueta"

    def __str__(self):
        return "Diseño de etiqueta 6x4"
