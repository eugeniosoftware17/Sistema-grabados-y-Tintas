"""
Pruebas de la fase 2 (alta de grabado y decisión de K1).

Los datos externos (API / externa_2012) se simulan con mock: las pruebas solo
usan la base `default` (test_CigarRingsEIS), que Django crea y borra sola.
"""
import json
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from apps.gestion_grabados.models import (
    EnvioMaquina, EstadoBano, FabricacionGrabado, Grabado, Maquina, OrdenFabricacion, PruebaK1,
)
from apps.gestion_grabados.services import grabados as servicio
from apps.gestion_grabados.views import VACIO_INFO_EXTERNA, _normalizar_of

GRUPO_SUPERVISORES = 'Supervisores de Producción'
RUTA_BUSCAR_EXTERNOS = 'apps.gestion_grabados.views_grabados.buscar_datos_externos'


def info_externa(encontrado=True, of_stamping='—', of_embossing='—', proceso=None,
                 cliente='CLIENTE SA', referencia='ANILLA DORADA', sobre='S-10'):
    """Misma forma que devuelve buscar_datos_externos()."""
    if not encontrado:
        return dict(VACIO_INFO_EXTERNA)
    return {
        'encontrado_ext': True, 'sobre_ext': sobre, 'ref_ext': of_stamping, 'acabado_ext': '0',
        'proceso_ext': proceso, 'cliente_ext': cliente, 'descripcion_ext': referencia,
        'of_stamping_ext': of_stamping, 'of_embossing_ext': of_embossing,
    }


def tecnicos(**cambios):
    # Pérdida 1000 g -> baño 6.6 ml -> compensación recomendada 7.
    datos = {
        'responsables': 'Juan, Pedro', 'tiempo': '45', 'peso_inicial': '3000',
        'peso_final': '2000', 'temp': '150', 'rpm': '800', 'compensacion': '7',
        'compensacion_motivo': '',
    }
    datos.update(cambios)
    return datos


class BaseGrabados(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.maquina = Maquina.objects.create(nombre='GIETZ 01')
        cls.maquina_inactiva = Maquina.objects.create(nombre='GIETZ 99', activa=False)
        cls.operario = User.objects.create_user('operario', password='x')
        cls.supervisor = User.objects.create_user('supervisor', password='x')
        cls.supervisor.groups.add(Group.objects.get(name=GRUPO_SUPERVISORES))
        cls.admin = User.objects.create_superuser('admin', password='x')

    def registrar(self, of='22741', proceso='STAMPING', info=None, usuario=None,
                  maquina_id=None, datos_manuales=None, **cambios_tecnicos):
        return servicio.registrar_fabricacion(
            of_origen=of, proceso=proceso,
            info_externa=info if info is not None else info_externa(),
            normalizar_of=_normalizar_of,
            datos_tecnicos=tecnicos(**cambios_tecnicos),
            maquina_id=maquina_id if maquina_id is not None else self.maquina.id,
            datos_manuales=datos_manuales or {},
            usuario=usuario or self.operario,
        )


# ============================================================
# SERVICIO: registrar_fabricacion
# ============================================================

class RegistrarFabricacionTests(BaseGrabados):

    def test_alta_nueva_crea_grabado_fabricacion_inicial_y_k1(self):
        resultado = self.registrar()

        grabado = Grabado.objects.get(of_origen='22741', proceso='STAMPING')
        self.assertEqual(resultado['accion'], servicio.ACCION_ALTA)
        self.assertEqual(grabado.estado, 'PENDIENTE_K1')
        self.assertEqual(grabado.cliente, 'CLIENTE SA')
        self.assertEqual(grabado.referencia, 'ANILLA DORADA')
        self.assertEqual(grabado.sobre, 'S-10')
        self.assertFalse(grabado.datos_manuales)
        self.assertEqual(grabado.creado_por, self.operario)

        fabricacion = grabado.fabricaciones.get()
        self.assertEqual((fabricacion.numero, fabricacion.tipo), (1, 'INICIAL'))
        self.assertEqual(fabricacion.perdida, 1000)
        self.assertAlmostEqual(fabricacion.bano_ml, 6.6)
        self.assertEqual(fabricacion.registrado_por, self.operario)

        prueba = grabado.pruebas_k1.get()
        self.assertEqual((prueba.intento, prueba.resultado), (1, 'PENDIENTE'))
        self.assertEqual(prueba.fabricacion, fabricacion)
        self.assertEqual(prueba.maquina, self.maquina)

    def test_alta_suma_al_bano(self):
        resultado = self.registrar()
        self.assertAlmostEqual(EstadoBano.obtener().ml_acumulados, 6.6)
        self.assertEqual(resultado['bano']['limite'], EstadoBano.LIMITE_ML)

    def test_of_no_encontrada_exige_cliente_y_referencia(self):
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'cliente y referencia'):
            self.registrar(info=info_externa(encontrado=False), datos_manuales={'cliente': 'X'})
        self.assertFalse(Grabado.objects.exists())

    def test_of_no_encontrada_con_datos_a_mano_marca_datos_manuales(self):
        self.registrar(info=info_externa(encontrado=False),
                       datos_manuales={'cliente': 'Cliente manual', 'referencia': 'Ref manual'})
        grabado = Grabado.objects.get()
        self.assertTrue(grabado.datos_manuales)
        self.assertEqual(grabado.cliente, 'Cliente manual')

    def test_of_encontrada_sin_datos_se_trata_como_manual(self):
        # Caso real OF 21221: la fila existe en CigarRings2012 pero con todo en NULL.
        vacia = info_externa(cliente='—', referencia='—', sobre='—')
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'escribirlos a mano'):
            self.registrar(info=vacia)
        self.registrar(info=vacia, datos_manuales={'cliente': 'Cliente manual', 'referencia': 'Ref manual'})
        grabado = Grabado.objects.get()
        self.assertTrue(grabado.datos_manuales)
        self.assertEqual((grabado.cliente, grabado.referencia, grabado.sobre),
                         ('Cliente manual', 'Ref manual', None))

    def test_of_con_datos_parciales_completa_solo_lo_que_falta(self):
        parcial = info_externa(referencia='—')
        self.registrar(info=parcial, datos_manuales={'cliente': 'Ignorado', 'referencia': 'Ref manual'})
        grabado = Grabado.objects.get()
        self.assertEqual(grabado.cliente, 'CLIENTE SA')  # lo de la API no se pisa
        self.assertEqual(grabado.referencia, 'Ref manual')
        self.assertTrue(grabado.datos_manuales)

    def test_bloquea_si_la_of_no_es_de_origen(self):
        info = info_externa(of_stamping='21000')
        evaluacion = servicio.evaluar_alta('22741', 'STAMPING', info, _normalizar_of)
        self.assertEqual(evaluacion['bloqueo'], servicio.BLOQUEO_NO_ES_ORIGEN)
        self.assertEqual(evaluacion['of_origen_sugerida'], '21000')
        with self.assertRaises(servicio.TransicionInvalida):
            self.registrar(info=info)

    def test_of_referencia_igual_a_la_propia_es_alta(self):
        resultado = self.registrar(info=info_externa(of_stamping='22741.0'))
        self.assertEqual(resultado['accion'], servicio.ACCION_ALTA)

    def test_bloquea_si_hay_historial_en_el_sistema_viejo(self):
        OrdenFabricacion.objects.create(of='23000', referencia='22741', proceso='STAMPING',
                                        cliente='C', descripcion='D')
        evaluacion = servicio.evaluar_alta('22741', 'STAMPING', info_externa(), _normalizar_of)
        self.assertEqual(evaluacion['bloqueo'], servicio.BLOQUEO_HISTORIAL_LEGADO)
        with self.assertRaisesMessage(servicio.TransicionInvalida, 'sistema anterior'):
            self.registrar()
        # El historial del otro proceso no bloquea.
        self.assertEqual(self.registrar(proceso='EMBOSSING')['accion'], servicio.ACCION_ALTA)

    def test_campos_obligatorios(self):
        for campo in ('responsables', 'peso_inicial', 'peso_final', 'temp', 'rpm'):
            with self.subTest(campo=campo):
                with self.assertRaisesMessage(servicio.ErrorGrabado, 'obligatorio'):
                    self.registrar(**{campo: ''})
        self.assertFalse(Grabado.objects.exists())

    def test_valores_numericos_invalidos(self):
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'entero'):
            self.registrar(rpm='800.5')
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'número'):
            self.registrar(temp='abc')
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'mayor o igual a 0'):
            self.registrar(peso_final='-1')

    def test_compensacion_cambiada_exige_motivo(self):
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'motivo'):
            self.registrar(compensacion='20')
        self.registrar(compensacion='20', compensacion_motivo='Baño muy usado')
        self.assertEqual(FabricacionGrabado.objects.get().compensacion_motivo, 'Baño muy usado')

    def test_compensacion_recomendada_redondea_como_javascript(self):
        # 378.787... g de pérdida -> 2.5 ml: Math.round da 3 (round() de Python daría 2).
        self.assertEqual(servicio.compensacion_recomendada(2.5 / 6.6 * 1000), 3)

    def test_maquina_obligatoria_y_activa(self):
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'no existe o no está activa'):
            self.registrar(maquina_id=self.maquina_inactiva.id)
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'máquina'):
            self.registrar(maquina_id='')
        self.assertFalse(Grabado.objects.exists())

    def test_refabricacion_tras_k1_rechazado_abre_nuevo_intento(self):
        primera = self.registrar()
        servicio.decidir_k1(prueba_id=primera['prueba_k1'].id, usuario=self.supervisor,
                            aprobar=False, motivo='Relieve incompleto')

        resultado = self.registrar(maquina_id=self.maquina.id)
        grabado = Grabado.objects.get()
        self.assertEqual(resultado['accion'], servicio.ACCION_RECHAZO_K1)
        self.assertEqual(grabado.estado, 'PENDIENTE_K1')
        self.assertEqual(resultado['fabricacion'].numero, 2)
        self.assertEqual(resultado['fabricacion'].tipo, 'RECHAZO_K1')
        self.assertEqual(resultado['prueba_k1'].intento, 2)
        self.assertEqual(grabado.pruebas_k1.filter(resultado='PENDIENTE').count(), 1)

    def test_refabricacion_por_repetir_vuelve_a_aprobado_sin_k1(self):
        primera = self.registrar()
        servicio.decidir_k1(prueba_id=primera['prueba_k1'].id, usuario=self.supervisor, aprobar=True)
        Grabado.objects.update(estado='REPETIR')

        resultado = self.registrar(maquina_id='')  # sin K1 la máquina no hace falta
        grabado = Grabado.objects.get()
        self.assertEqual(resultado['accion'], servicio.ACCION_REPETICION)
        self.assertIsNone(resultado['prueba_k1'])
        self.assertEqual(grabado.estado, 'APROBADO')
        self.assertEqual(resultado['fabricacion'].tipo, 'REPETICION')
        self.assertEqual(grabado.fabricaciones.count(), 2)
        self.assertEqual(grabado.pruebas_k1.count(), 1)

    def test_otros_estados_no_permiten_registrar(self):
        self.registrar()
        for estado in ('PENDIENTE_K1', 'APROBADO', 'EN_MAQUINA'):
            with self.subTest(estado=estado):
                Grabado.objects.update(estado=estado)
                with self.assertRaises(servicio.TransicionInvalida):
                    self.registrar()
        self.assertEqual(FabricacionGrabado.objects.count(), 1)


# ============================================================
# SERVICIO: decidir_k1
# ============================================================

class DecidirK1Tests(BaseGrabados):

    def setUp(self):
        self.prueba = self.registrar()['prueba_k1']

    def test_aprobar(self):
        servicio.decidir_k1(prueba_id=self.prueba.id, usuario=self.supervisor, aprobar=True)
        self.prueba.refresh_from_db()
        self.assertEqual(self.prueba.resultado, 'APROBADO')
        self.assertEqual(self.prueba.decidido_por, self.supervisor)
        self.assertIsNotNone(self.prueba.decidido_el)
        self.assertEqual(self.prueba.grabado.estado, 'APROBADO')

    def test_rechazar_guarda_motivo_y_vuelve_a_en_fabricacion(self):
        servicio.decidir_k1(prueba_id=self.prueba.id, usuario=self.supervisor,
                            aprobar=False, motivo='  Falta definición  ')
        self.prueba.refresh_from_db()
        self.assertEqual(self.prueba.resultado, 'RECHAZADO')
        self.assertEqual(self.prueba.motivo_rechazo, 'Falta definición')
        self.assertEqual(Grabado.objects.get().estado, 'EN_FABRICACION')

    def test_rechazar_sin_motivo_falla(self):
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'motivo'):
            servicio.decidir_k1(prueba_id=self.prueba.id, usuario=self.supervisor,
                                aprobar=False, motivo='mal')
        self.prueba.refresh_from_db()
        self.assertEqual(self.prueba.resultado, 'PENDIENTE')

    def test_no_se_decide_dos_veces(self):
        servicio.decidir_k1(prueba_id=self.prueba.id, usuario=self.supervisor, aprobar=True)
        with self.assertRaisesMessage(servicio.TransicionInvalida, 'ya fue aprobado'):
            servicio.decidir_k1(prueba_id=self.prueba.id, usuario=self.admin,
                                aprobar=False, motivo='Cambio de opinión')

    def test_quien_registro_no_puede_decidir_aunque_sea_supervisor_o_superusuario(self):
        for autor in (self.supervisor, self.admin):
            with self.subTest(autor=autor.username):
                prueba = self.registrar(of=f'3{autor.pk}000', usuario=autor)['prueba_k1']
                with self.assertRaises(servicio.AutoDecisionProhibida):
                    servicio.decidir_k1(prueba_id=prueba.id, usuario=autor, aprobar=True)
                with self.assertRaises(servicio.AutoDecisionProhibida):
                    servicio.decidir_k1(prueba_id=prueba.id, usuario=autor,
                                        aprobar=False, motivo='Rechazo propio')
                prueba.refresh_from_db()
                self.assertEqual(prueba.resultado, 'PENDIENTE')

    def test_k1_inexistente(self):
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'no existe'):
            servicio.decidir_k1(prueba_id=999999, usuario=self.supervisor, aprobar=True)


# ============================================================
# VISTAS / PERMISOS
# ============================================================

class VistasAltaTests(BaseGrabados):

    def setUp(self):
        self.client.force_login(self.operario)

    def test_requiere_login(self):
        self.client.logout()
        for nombre in ('alta_grabado', 'api_alta_buscar', 'k1_pendientes', 'api_k1_pendientes'):
            with self.subTest(vista=nombre):
                respuesta = self.client.get(reverse(f'grabados:{nombre}'))
                self.assertEqual(respuesta.status_code, 302)
                self.assertIn('login', respuesta['Location'])

    def test_pagina_alta_lista_solo_maquinas_activas(self):
        respuesta = self.client.get(reverse('grabados:alta_grabado'))
        self.assertContains(respuesta, 'GIETZ 01')
        self.assertNotContains(respuesta, 'GIETZ 99')
        self.assertNotContains(respuesta, 'No hay máquinas activas')

    def test_pagina_alta_avisa_si_no_hay_maquinas(self):
        Maquina.objects.update(activa=False)
        respuesta = self.client.get(reverse('grabados:alta_grabado'))
        self.assertContains(respuesta, 'No hay máquinas activas')

    @mock.patch(RUTA_BUSCAR_EXTERNOS, return_value=info_externa(of_stamping='22741', proceso='STAMPING'))
    def test_buscar_devuelve_evaluacion_por_proceso(self, _mock):
        respuesta = self.client.get(reverse('grabados:api_alta_buscar'), {'of': '22741.0'}).json()
        self.assertEqual(respuesta['of'], '22741')
        self.assertEqual(respuesta['externo']['proceso'], 'STAMPING')
        self.assertEqual(respuesta['externo']['cliente'], 'CLIENTE SA')
        self.assertEqual(respuesta['procesos']['STAMPING']['accion'], servicio.ACCION_ALTA)
        self.assertTrue(respuesta['hay_maquinas'])

    @mock.patch(RUTA_BUSCAR_EXTERNOS, return_value=info_externa(cliente='—', referencia='—', sobre='—'))
    def test_buscar_of_encontrada_sin_datos_no_figura_como_completa(self, _mock):
        externo = self.client.get(reverse('grabados:api_alta_buscar'), {'of': '21221'}).json()['externo']
        self.assertTrue(externo['encontrado'])
        self.assertFalse(externo['completo'])
        self.assertEqual((externo['cliente'], externo['referencia'], externo['sobre']), ('', '', ''))

    def test_buscar_of_invalida(self):
        respuesta = self.client.get(reverse('grabados:api_alta_buscar'), {'of': 'abc'})
        self.assertEqual(respuesta.status_code, 400)

    @mock.patch(RUTA_BUSCAR_EXTERNOS, return_value=info_externa())
    def test_registrar_por_api(self, _mock):
        respuesta = self.client.post(
            reverse('grabados:api_alta_registrar'),
            data=json.dumps({'of': '22741', 'proceso': 'STAMPING', 'maquina_id': self.maquina.id,
                             'tecnicos': tecnicos()}),
            content_type='application/json',
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.json()['estado'], 'PENDIENTE_K1')
        self.assertIn('bano', respuesta.json())

    @mock.patch(RUTA_BUSCAR_EXTERNOS, return_value=info_externa())
    def test_registrar_por_api_con_error_de_validacion(self, _mock):
        respuesta = self.client.post(
            reverse('grabados:api_alta_registrar'),
            data=json.dumps({'of': '22741', 'proceso': 'STAMPING', 'maquina_id': self.maquina.id,
                             'tecnicos': tecnicos(temp='')}),
            content_type='application/json',
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('Temperatura', respuesta.json()['message'])
        self.assertFalse(Grabado.objects.exists())

    def test_registrar_exige_post(self):
        self.assertEqual(self.client.get(reverse('grabados:api_alta_registrar')).status_code, 405)


class VistasK1Tests(BaseGrabados):

    def setUp(self):
        self.prueba = self.registrar()['prueba_k1']

    def test_grupo_supervisores_tiene_el_permiso(self):
        grupo = Group.objects.get(name=GRUPO_SUPERVISORES)
        self.assertTrue(grupo.permissions.filter(codename='decidir_pruebak1').exists())

    def test_sin_permiso_no_entra(self):
        self.client.force_login(self.operario)
        self.assertEqual(self.client.get(reverse('grabados:k1_pendientes')).status_code, 403)
        self.assertEqual(self.client.get(reverse('grabados:api_k1_pendientes')).status_code, 403)
        respuesta = self.client.post(reverse('grabados:api_k1_aprobar', args=[self.prueba.id]),
                                     data='{}', content_type='application/json')
        self.assertEqual(respuesta.status_code, 403)
        self.prueba.refresh_from_db()
        self.assertEqual(self.prueba.resultado, 'PENDIENTE')

    def test_menu_muestra_k1_solo_con_permiso(self):
        url_k1 = reverse('grabados:k1_pendientes')
        self.client.force_login(self.operario)
        self.assertNotContains(self.client.get(reverse('grabados:alta_grabado')), url_k1)
        self.client.force_login(self.supervisor)
        self.assertContains(self.client.get(reverse('grabados:alta_grabado')), url_k1)

    def test_supervisor_lista_y_aprueba(self):
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get(reverse('grabados:k1_pendientes')).status_code, 200)

        datos = self.client.get(reverse('grabados:api_k1_pendientes')).json()['data']
        self.assertEqual(len(datos), 1)
        self.assertEqual(datos[0]['of'], '22741')
        self.assertEqual(datos[0]['registrado_por'], 'operario')
        self.assertFalse(datos[0]['es_propio'])
        self.assertEqual(datos[0]['grabado_id'], self.prueba.grabado_id)
        self.assertNotIn('peso_inicial', datos[0])  # los datos técnicos van en el detalle

        respuesta = self.client.post(reverse('grabados:api_k1_aprobar', args=[self.prueba.id]),
                                     data='{}', content_type='application/json')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(Grabado.objects.get().estado, 'APROBADO')
        self.assertEqual(self.client.get(reverse('grabados:api_k1_pendientes')).json()['data'], [])

    def test_supervisor_rechaza_con_motivo(self):
        self.client.force_login(self.supervisor)
        url = reverse('grabados:api_k1_rechazar', args=[self.prueba.id])
        sin_motivo = self.client.post(url, data='{}', content_type='application/json')
        self.assertEqual(sin_motivo.status_code, 400)
        respuesta = self.client.post(url, data=json.dumps({'motivo': 'Relieve incompleto'}),
                                     content_type='application/json')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(Grabado.objects.get().estado, 'EN_FABRICACION')

    def test_auto_aprobacion_por_api_da_mensaje_claro(self):
        propia = self.registrar(of='30000', usuario=self.admin)['prueba_k1']
        self.client.force_login(self.admin)
        datos = {d['id']: d for d in self.client.get(reverse('grabados:api_k1_pendientes')).json()['data']}
        self.assertTrue(datos[propia.id]['es_propio'])

        respuesta = self.client.post(reverse('grabados:api_k1_aprobar', args=[propia.id]),
                                     data='{}', content_type='application/json')
        self.assertEqual(respuesta.status_code, 403)
        self.assertIn('registraste tú', respuesta.json()['message'])


# ============================================================
# DETALLE DE GRABADO (endpoint de solo lectura)
# ============================================================

class DetalleGrabadoTests(BaseGrabados):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.consulta = User.objects.create_user('consulta', password='x')  # sin permiso de K1

    def setUp(self):
        # Fabricación 1 -> K1 1 rechazado -> fabricación 2 -> K1 2 pendiente.
        primera = self.registrar()
        self.grabado = primera['grabado']
        servicio.decidir_k1(prueba_id=primera['prueba_k1'].id, usuario=self.supervisor,
                            aprobar=False, motivo='Relieve incompleto')
        segunda = self.registrar(peso_inicial='5000', peso_final='4000', temp='160', rpm='900')
        self.k1_pendiente = segunda['prueba_k1']

    def url(self, grabado_id=None):
        return reverse('grabados:api_grabado_detalle', args=[grabado_id or self.grabado.id])

    def detalle(self, usuario):
        self.client.force_login(usuario)
        respuesta = self.client.get(self.url())
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return respuesta.json()['data']

    def test_requiere_login(self):
        respuesta = self.client.get(self.url())
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('login', respuesta['Location'])

    def test_grabado_inexistente_da_404(self):
        self.client.force_login(self.consulta)
        respuesta = self.client.get(self.url(999999))
        self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(respuesta.json()['status'], 'error')

    def test_es_solo_lectura(self):
        self.client.force_login(self.supervisor)
        respuesta = self.client.post(self.url(), data='{}', content_type='application/json')
        self.assertEqual(respuesta.status_code, 405)
        self.assertEqual(PruebaK1.objects.get(pk=self.k1_pendiente.pk).resultado, 'PENDIENTE')

    def test_no_requiere_permiso_de_k1(self):
        d = self.detalle(self.consulta)
        self.assertEqual(d['of_origen'], '22741')
        self.assertFalse(d['k1_actual']['puede_decidir'])
        self.assertFalse(d['k1_actual']['es_propio'])

    def test_datos_generales(self):
        d = self.detalle(self.supervisor)
        self.assertEqual(d['id'], self.grabado.id)
        self.assertEqual((d['proceso'], d['estado'], d['estado_display']),
                         ('STAMPING', 'PENDIENTE_K1', 'Pendiente de K1'))
        self.assertEqual((d['cliente'], d['referencia'], d['sobre']), ('CLIENTE SA', 'ANILLA DORADA', 'S-10'))
        self.assertFalse(d['datos_manuales'])
        self.assertEqual(d['creado_por'], 'operario')

    def test_k1_actual_con_la_fabricacion_en_prueba(self):
        k1 = self.detalle(self.supervisor)['k1_actual']
        self.assertEqual(k1['id'], self.k1_pendiente.id)
        self.assertEqual((k1['intento'], k1['maquina'], k1['resultado']), (2, 'GIETZ 01', 'PENDIENTE'))
        self.assertEqual(k1['creado_por'], 'operario')
        self.assertTrue(k1['puede_decidir'])
        self.assertEqual(k1['fabricacion']['numero'], 2)
        tecnicos = k1['fabricacion']['tecnicos']
        self.assertEqual((tecnicos['peso_inicial'], tecnicos['peso_final'], tecnicos['perdida']), (5000, 4000, 1000))
        self.assertEqual((tecnicos['temp'], tecnicos['rpm']), (160, 900))
        self.assertAlmostEqual(tecnicos['bano_ml'], 6.6)

    def test_quien_registro_no_puede_decidir(self):
        k1 = self.detalle(self.operario)['k1_actual']
        self.assertTrue(k1['es_propio'])
        self.assertFalse(k1['puede_decidir'])

    def test_historiales_de_fabricaciones_e_intentos(self):
        d = self.detalle(self.supervisor)
        self.assertEqual([(f['numero'], f['tipo']) for f in d['fabricaciones']],
                         [(1, 'INICIAL'), (2, 'RECHAZO_K1')])
        self.assertEqual(d['fabricaciones'][0]['tecnicos']['temp'], 150)
        self.assertEqual(d['fabricaciones'][0]['registrado_por'], 'operario')

        intentos = d['pruebas_k1']
        self.assertEqual([(p['intento'], p['resultado']) for p in intentos],
                         [(1, 'RECHAZADO'), (2, 'PENDIENTE')])
        self.assertEqual(intentos[0]['motivo_rechazo'], 'Relieve incompleto')
        self.assertEqual(intentos[0]['decidido_por'], 'supervisor')
        self.assertIsNotNone(intentos[0]['decidido_el'])
        self.assertIsNone(intentos[1]['decidido_por'])

    def test_envios_usos_y_ubicacion(self):
        Grabado.objects.filter(pk=self.grabado.pk).update(usos_acumulados=3, ubicacion='Cajón A1')
        EnvioMaquina.objects.create(grabado=self.grabado, of='23000', maquina=self.maquina,
                                    enviado_por=self.operario, enviado_el='2026-10-01T10:00:00Z')
        d = self.detalle(self.supervisor)
        self.assertEqual((d['usos_acumulados'], d['ubicacion']), (3, 'Cajón A1'))
        self.assertEqual(len(d['envios']), 1)
        envio = d['envios'][0]
        self.assertEqual((envio['of'], envio['maquina'], envio['enviado_por']), ('23000', 'GIETZ 01', 'operario'))
        self.assertTrue(envio['abierto'])
        self.assertEqual(envio['enviado_el'], '01/10/2026 10:00')

    def test_sin_k1_pendiente(self):
        servicio.decidir_k1(prueba_id=self.k1_pendiente.id, usuario=self.supervisor, aprobar=True)
        d = self.detalle(self.supervisor)
        self.assertIsNone(d['k1_actual'])
        self.assertEqual(d['estado'], 'APROBADO')
        self.assertEqual(d['envios'], [])
