"""
Pruebas de los modelos nuevos: alta de grabado y decisión de K1 (fase 2),
inventario, y PLANI con envío a máquina y recogida (fase 3), y el comando
migrar_a_grabados (fase 4).

Los datos externos (API / externa_2012) se simulan con mock: las pruebas solo
usan la base `default` (test_CigarRingsEIS), que Django crea y borra sola.
"""
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
from unittest import mock

import pandas as pd
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.gestion_grabados import selectors
from apps.gestion_grabados.models import (
    EnvioMaquina, EstadoBano, FabricacionGrabado, Grabado, LegadoOrden, Maquina, OrdenFabricacion,
    PruebaK1,
)
from apps.gestion_grabados.services import grabados as servicio
from apps.gestion_grabados.views import VACIO_INFO_EXTERNA, _normalizar_of, limpiar_texto_excel

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

    def test_bloquea_si_la_of_no_tiene_el_proceso_en_el_sistema_externo(self):
        # La OF existe y tiene EMBOSSING, pero se eligió STAMPING.
        info = info_externa(of_embossing='22741')
        evaluacion = servicio.evaluar_alta('22741', 'STAMPING', info, _normalizar_of)
        self.assertEqual(evaluacion['accion'], servicio.ACCION_BLOQUEADA)
        self.assertEqual(evaluacion['bloqueo'], servicio.BLOQUEO_SIN_PROCESO)
        self.assertEqual(evaluacion['mensaje'],
                         'Esta OF no tiene STAMPING en el sistema externo; revisa el proceso.')
        with self.assertRaisesMessage(servicio.TransicionInvalida, 'no tiene STAMPING'):
            self.registrar(info=info)
        self.assertFalse(Grabado.objects.exists())
        # Con el proceso correcto, sí.
        self.assertEqual(self.registrar(proceso='EMBOSSING', info=info)['accion'], servicio.ACCION_ALTA)

    def test_sin_proceso_no_bloquea_si_la_of_no_existe_o_no_tiene_ningun_proceso(self):
        # OF no encontrada: alta manual como siempre.
        self.registrar(of='22741', info=info_externa(encontrado=False),
                       datos_manuales={'cliente': 'Cliente manual', 'referencia': 'Ref manual'})
        # OF encontrada sin ningún proceso informado: no hay base para bloquear.
        self.assertEqual(self.registrar(of='22742', info=info_externa())['accion'], servicio.ACCION_ALTA)

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

    def test_supervisor_no_superusuario_no_decide_su_propia_fabricacion(self):
        prueba = self.registrar(of='31000', usuario=self.supervisor)['prueba_k1']
        self.assertFalse(servicio.puede_decidir_k1(self.supervisor, prueba))
        with self.assertRaises(servicio.AutoDecisionProhibida):
            servicio.decidir_k1(prueba_id=prueba.id, usuario=self.supervisor, aprobar=True)
        with self.assertRaises(servicio.AutoDecisionProhibida):
            servicio.decidir_k1(prueba_id=prueba.id, usuario=self.supervisor,
                                aprobar=False, motivo='Rechazo propio')
        prueba.refresh_from_db()
        self.assertEqual(prueba.resultado, 'PENDIENTE')
        self.assertIsNone(prueba.decidido_por)

    def test_superusuario_aprueba_su_propia_fabricacion(self):
        prueba = self.registrar(of='32000', usuario=self.admin)['prueba_k1']
        self.assertTrue(servicio.puede_decidir_k1(self.admin, prueba))
        servicio.decidir_k1(prueba_id=prueba.id, usuario=self.admin, aprobar=True)
        prueba.refresh_from_db()
        self.assertEqual(prueba.resultado, 'APROBADO')
        # Sin campo extra: la auto-decisión se deduce de decidido_por == registrado_por.
        self.assertEqual(prueba.decidido_por, prueba.fabricacion.registrado_por)
        self.assertTrue(servicio.es_auto_decision(prueba))
        self.assertEqual(prueba.grabado.estado, 'APROBADO')

    def test_superusuario_rechaza_su_propia_fabricacion(self):
        prueba = self.registrar(of='33000', usuario=self.admin)['prueba_k1']
        servicio.decidir_k1(prueba_id=prueba.id, usuario=self.admin, aprobar=False, motivo='Rechazo propio')
        prueba.refresh_from_db()
        self.assertEqual(prueba.resultado, 'RECHAZADO')
        self.assertTrue(servicio.es_auto_decision(prueba))
        self.assertEqual(prueba.grabado.estado, 'EN_FABRICACION')

    def test_decision_de_otro_no_es_auto_decision(self):
        servicio.decidir_k1(prueba_id=self.prueba.id, usuario=self.admin, aprobar=True)
        self.prueba.refresh_from_db()
        self.assertFalse(servicio.es_auto_decision(self.prueba))

    def test_sin_permiso_nadie_puede_decidir(self):
        self.assertFalse(servicio.puede_decidir_k1(self.operario, self.prueba))

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
        # La OF solo tiene STAMPING en el sistema externo: EMBOSSING queda bloqueado.
        self.assertEqual(respuesta['procesos']['EMBOSSING']['bloqueo'], servicio.BLOQUEO_SIN_PROCESO)
        self.assertIn('no tiene EMBOSSING', respuesta['procesos']['EMBOSSING']['mensaje'])
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

    def test_auto_aprobacion_de_supervisor_por_api_da_mensaje_claro(self):
        propia = self.registrar(of='30000', usuario=self.supervisor)['prueba_k1']
        self.client.force_login(self.supervisor)
        datos = {d['id']: d for d in self.client.get(reverse('grabados:api_k1_pendientes')).json()['data']}
        self.assertTrue(datos[propia.id]['es_propio'])
        self.assertFalse(datos[propia.id]['puede_decidir'])
        self.assertTrue(datos[self.prueba.id]['puede_decidir'])  # el de otro sí

        respuesta = self.client.post(reverse('grabados:api_k1_aprobar', args=[propia.id]),
                                     data='{}', content_type='application/json')
        self.assertEqual(respuesta.status_code, 403)
        self.assertIn('registraste tú', respuesta.json()['message'])

    def test_superusuario_decide_su_propio_k1_por_api(self):
        propia = self.registrar(of='30001', usuario=self.admin)['prueba_k1']
        self.client.force_login(self.admin)
        datos = {d['id']: d for d in self.client.get(reverse('grabados:api_k1_pendientes')).json()['data']}
        self.assertTrue(datos[propia.id]['es_propio'])
        self.assertTrue(datos[propia.id]['puede_decidir'])

        respuesta = self.client.post(reverse('grabados:api_k1_rechazar', args=[propia.id]),
                                     data=json.dumps({'motivo': 'Lo rechazo yo mismo'}),
                                     content_type='application/json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        propia.refresh_from_db()
        self.assertEqual((propia.resultado, propia.decidido_por), ('RECHAZADO', self.admin))


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

    def test_superusuario_autor_puede_decidir_y_queda_como_auto_decision(self):
        prueba = self.registrar(of='34000', usuario=self.admin)['prueba_k1']
        self.client.force_login(self.admin)
        url = reverse('grabados:api_grabado_detalle', args=[prueba.grabado_id])
        k1 = self.client.get(url).json()['data']['k1_actual']
        self.assertTrue(k1['es_propio'])
        self.assertTrue(k1['puede_decidir'])

        servicio.decidir_k1(prueba_id=prueba.id, usuario=self.admin, aprobar=True)
        intentos = self.client.get(url).json()['data']['pruebas_k1']
        self.assertEqual((intentos[0]['resultado'], intentos[0]['auto_decision']), ('APROBADO', True))

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
        self.assertFalse(intentos[0]['auto_decision'])  # lo decidió otro supervisor
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


# ============================================================
# INVENTARIO DE GRABADOS
# ============================================================

class InventarioGrabadosTests(BaseGrabados):

    def setUp(self):
        # 22741 STAMPING: K1 1 rechazado + K1 2 pendiente -> PENDIENTE_K1
        primera = self.registrar(of='22741')
        servicio.decidir_k1(prueba_id=primera['prueba_k1'].id, usuario=self.supervisor,
                            aprobar=False, motivo='Relieve incompleto')
        self.registrar(of='22741')
        # 22800 EMBOSSING de otro cliente: K1 aprobado -> APROBADO, con usos y ubicación
        otro = info_externa(cliente='MY FATHER CIGARS', referencia='FLOR DE LAS ANTILLAS', sobre='S-2')
        aprobado = self.registrar(of='22800', proceso='EMBOSSING', info=otro)
        servicio.decidir_k1(prueba_id=aprobado['prueba_k1'].id, usuario=self.supervisor, aprobar=True)
        Grabado.objects.filter(of_origen='22800').update(usos_acumulados=4, ubicacion='Cajón B2')
        # 22900 STAMPING sin K1 todavía decidido -> PENDIENTE_K1, intento 1
        self.registrar(of='22900')
        self.client.force_login(self.operario)   # usuario común, sin permiso de K1

    def api(self, **params):
        respuesta = self.client.get(reverse('grabados:api_inventario'), params)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return respuesta.json()

    def ofs(self, respuesta):
        return sorted(g['of_origen'] for g in respuesta['data'])

    def test_requiere_login(self):
        self.client.logout()
        for nombre in ('inventario_grabados', 'api_inventario'):
            with self.subTest(vista=nombre):
                respuesta = self.client.get(reverse(f'grabados:{nombre}'))
                self.assertEqual(respuesta.status_code, 302)
                self.assertIn('login', respuesta['Location'])

    def test_pagina_visible_para_cualquier_usuario_y_en_el_menu(self):
        respuesta = self.client.get(reverse('grabados:inventario_grabados'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Inventario de Grabados')
        self.assertContains(respuesta, reverse('grabados:inventario_grabados'))  # enlace del menú
        for codigo, _ in Grabado.ESTADO_CHOICES:
            self.assertContains(respuesta, f'data-estado="{codigo}"')

    def test_conteos_por_estado(self):
        res = self.api()
        self.assertEqual(res['conteos'], {
            'EN_FABRICACION': 0, 'PENDIENTE_K1': 2, 'APROBADO': 1, 'EN_MAQUINA': 0, 'REPETIR': 0,
        })
        self.assertEqual(res['total'], 3)

    def test_columnas_de_la_fila(self):
        filas = {g['of_origen']: g for g in self.api()['data']}
        g = filas['22741']
        self.assertEqual((g['proceso'], g['cliente'], g['referencia']), ('STAMPING', 'CLIENTE SA', 'ANILLA DORADA'))
        self.assertEqual((g['estado'], g['estado_display']), ('PENDIENTE_K1', 'Pendiente de K1'))
        self.assertEqual(g['intentos_k1'], 2)
        self.assertEqual((g['ultimo_k1'], g['ultimo_k1_display']), ('PENDIENTE', 'Pendiente'))
        self.assertRegex(g['creado_el'], r'^\d{2}/\d{2}/\d{4}$')

        aprobado = filas['22800']
        self.assertEqual((aprobado['intentos_k1'], aprobado['ultimo_k1']), (1, 'APROBADO'))
        self.assertEqual((aprobado['usos_acumulados'], aprobado['ubicacion']), (4, 'Cajón B2'))
        self.assertIn('id', aprobado)  # para abrir el panel de detalle

    def test_grabado_sin_k1_muestra_cero_intentos(self):
        Grabado.objects.create(of_origen='25000', proceso='STAMPING', cliente='X', estado='APROBADO',
                               aprobado_legado=True)
        fila = next(g for g in self.api()['data'] if g['of_origen'] == '25000')
        self.assertEqual((fila['intentos_k1'], fila['ultimo_k1']), (0, None))

    def test_filtro_por_estado_no_cambia_los_conteos(self):
        res = self.api(estado='APROBADO')
        self.assertEqual(self.ofs(res), ['22800'])
        self.assertEqual(res['total'], 1)
        self.assertEqual(res['conteos']['PENDIENTE_K1'], 2)  # las tarjetas siguen mostrando todo

    def test_busqueda_por_of_cliente_y_referencia(self):
        self.assertEqual(self.ofs(self.api(q='2290')), ['22900'])
        self.assertEqual(self.ofs(self.api(q='father')), ['22800'])
        self.assertEqual(self.ofs(self.api(q='antillas')), ['22800'])
        self.assertEqual(self.ofs(self.api(q='cliente dorada')), ['22741', '22900'])  # todas las palabras
        self.assertEqual(self.api(q='no-existe')['data'], [])

    def test_filtro_por_proceso_afecta_conteos(self):
        res = self.api(proceso='EMBOSSING')
        self.assertEqual(self.ofs(res), ['22800'])
        self.assertEqual(res['conteos']['PENDIENTE_K1'], 0)
        self.assertEqual(res['conteos']['APROBADO'], 1)

    def test_filtros_invalidos(self):
        for params in ({'proceso': 'OTRO'}, {'estado': 'PENDIENTE'}):
            with self.subTest(params=params):
                respuesta = self.client.get(reverse('grabados:api_inventario'), params)
                self.assertEqual(respuesta.status_code, 400)

    def test_es_solo_lectura(self):
        respuesta = self.client.post(reverse('grabados:api_inventario'), data='{}', content_type='application/json')
        self.assertEqual(respuesta.status_code, 405)

    def test_limite_de_filas(self):
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_INVENTARIO', 2):
            res = self.api()
        self.assertEqual(len(res['data']), 2)
        self.assertEqual(res['total'], 3)

    def test_orden_del_mas_nuevo_al_mas_viejo(self):
        self.assertEqual([g['of_origen'] for g in self.api()['data']], ['22900', '22800', '22741'])


# ============================================================
# PLANI (fase 3): resolución de filas, mandar a máquina y recoger
# ============================================================

def fila_plani(of, proceso='STAMPING', acabado_ext='0', **ext):
    """Fila del PLANI tal como la arma sincronizar_plani (Excel + datos externos)."""
    fila = {'of': of, 'proceso': proceso, 'maquina': 'GIETZ 01', **info_externa(**ext)}
    fila['acabado_ext'] = acabado_ext
    return fila


def fila_excel(**cambios):
    datos = {'maquina': 'GIETZ 01', 'fecha_programada': '15/10/2026', 'cantidad_formatos': 1200,
             'horas_proceso': 2.5, 'papel': 'Couché 90 g'}
    datos.update(cambios)
    return datos


class BasePlani(BaseGrabados):

    def aprobado(self, of='22741', proceso='STAMPING', info=None):
        """Grabado APROBADO: alta + K1 aprobado por el supervisor."""
        resultado = self.registrar(of=of, proceso=proceso, info=info)
        servicio.decidir_k1(prueba_id=resultado['prueba_k1'].id, usuario=self.supervisor, aprobar=True)
        return Grabado.objects.get(pk=resultado['grabado'].pk)

    def mandar(self, grabado, of='22741', proceso='STAMPING', **cambios_fila):
        return servicio.mandar_a_maquina(of=of, proceso=proceso, grabado_id=grabado.id,
                                         fila=fila_excel(**cambios_fila), usuario=self.operario)

    def recoger(self, envio, of='22741', estado='OK', ubicacion='Cajón A1', comentario=''):
        return servicio.recoger_de_maquina(envio_id=envio.id, of=of, estado_fisico=estado,
                                           ubicacion=ubicacion, comentario=comentario,
                                           usuario=self.operario)

    def resolver(self, *filas):
        filas = [dict(f) for f in filas]
        selectors.estado_grabados_para_plani(filas, _normalizar_of)
        return [f['grabado'] for f in filas]


class PlaniResolucionTests(BasePlani):

    def test_sin_grabado_pide_dar_de_alta_con_la_of_de_origen(self):
        propia, ajena = self.resolver(fila_plani('22741'), fila_plani('23000', of_stamping='22741'))
        self.assertEqual(propia['accion'], selectors.PLANI_DAR_DE_ALTA)
        self.assertEqual(propia['alta'], {'of': '22741', 'proceso': 'STAMPING'})
        # La OF 23000 usa el grabado de la 22741: el alta es de la 22741.
        self.assertEqual(ajena['alta'], {'of': '22741', 'proceso': 'STAMPING'})
        self.assertIn('22741', ajena['mensaje'])

    def test_of_no_encontrada_usa_la_propia_of(self):
        (estado,) = self.resolver(fila_plani('22741', encontrado=False))
        self.assertEqual(estado['alta'], {'of': '22741', 'proceso': 'STAMPING'})

    def test_proceso_que_no_esta_en_la_api(self):
        (estado,) = self.resolver(fila_plani('22741', proceso='STAMPING', of_embossing='22741'))
        self.assertEqual(estado['accion'], selectors.PLANI_SIN_PROCESO)
        self.assertEqual(estado['mensaje'], 'El sistema externo no tiene STAMPING para esta OF.')

    def test_en_fabricacion_o_pendiente_de_k1_sin_accion(self):
        self.registrar()   # queda PENDIENTE_K1
        (estado,) = self.resolver(fila_plani('22741'))
        self.assertEqual(estado['accion'], selectors.PLANI_SIN_ACCION)
        self.assertEqual(estado['grabado']['estado'], 'PENDIENTE_K1')

    def test_aprobado_se_puede_mandar_y_marca_si_usa_grabado_de_otra(self):
        grabado = self.aprobado()
        propia, ajena = self.resolver(fila_plani('22741'), fila_plani('23000', of_stamping='22741'))
        self.assertEqual((propia['accion'], propia['grabado']['id']), (selectors.PLANI_MANDAR, grabado.id))
        self.assertFalse(propia['grabado']['usa_grabado_de_otra'])
        self.assertEqual((ajena['accion'], ajena['grabado']['id']), (selectors.PLANI_MANDAR, grabado.id))
        self.assertTrue(ajena['grabado']['usa_grabado_de_otra'])

    def test_en_maquina_recoger_esta_of_y_bloquear_las_demas(self):
        grabado = self.aprobado()
        envio = self.mandar(grabado)
        propia, ajena = self.resolver(fila_plani('22741', acabado_ext='1'),
                                      fila_plani('23000', of_stamping='22741'))
        self.assertEqual(propia['accion'], selectors.PLANI_RECOGER)
        self.assertEqual(propia['envio']['id'], envio.id)
        self.assertEqual(propia['envio']['maquina'], 'GIETZ 01')
        self.assertTrue(propia['terminada_en_planta'])
        self.assertEqual(ajena['accion'], selectors.PLANI_EN_MAQUINA_OTRA)
        self.assertEqual(ajena['mensaje'], 'En máquina con la OF 22741')

    def test_recoger_aparece_aunque_se_haya_elegido_otro_grabado(self):
        self.aprobado()                                  # grabado por defecto de la 22741
        otro = self.aprobado(of='21000')                 # otro grabado aprobado
        self.mandar(otro, of='22741')
        (estado,) = self.resolver(fila_plani('22741'))
        self.assertEqual(estado['accion'], selectors.PLANI_RECOGER)
        self.assertEqual(estado['grabado']['id'], otro.id)
        self.assertTrue(estado['grabado']['usa_grabado_de_otra'])

    def test_of_completada_se_puede_mandar_otra_vez(self):
        envio = self.mandar(self.aprobado())
        self.recoger(envio, ubicacion='Cajón B2')
        (estado,) = self.resolver(fila_plani('22741'))
        self.assertEqual(estado['accion'], selectors.PLANI_MANDAR_OTRA_VEZ)
        self.assertEqual(estado['completada']['ubicacion'], 'Cajón B2')
        self.assertRegex(estado['completada']['fecha'], r'^\d{2}/\d{2}$')

    def test_repetir_pide_refabricar(self):
        envio = self.mandar(self.aprobado())
        self.recoger(envio, estado='REPETIR', comentario='Relieve gastado')
        (estado,) = self.resolver(fila_plani('22741'))
        self.assertEqual(estado['accion'], selectors.PLANI_REFABRICAR)
        self.assertEqual(estado['alta'], {'of': '22741', 'proceso': 'STAMPING'})


class PlaniMandarTests(BasePlani):

    def test_mandar_copia_los_datos_de_la_fila_y_deja_el_grabado_en_maquina(self):
        grabado = self.aprobado()
        envio = self.mandar(grabado, maquina='  gietz   01 ')   # misma normalización que el catálogo
        grabado.refresh_from_db()
        self.assertEqual(grabado.estado, 'EN_MAQUINA')
        self.assertEqual((envio.of, envio.maquina, envio.enviado_por), ('22741', self.maquina, self.operario))
        self.assertEqual(str(envio.fecha_programada), '2026-10-15')
        self.assertEqual((envio.cantidad_formatos, envio.horas_proceso, envio.papel), (1200, 2.5, 'Couché 90 g'))
        self.assertIsNone(envio.recogido_el)

    def test_maquina_fuera_del_catalogo_inactiva_o_vacia(self):
        grabado = self.aprobado()
        casos = (('GIETZ 03', 'no está en el catálogo'), ('GIETZ 99', 'inactiva'), ('', 'no tiene máquina'))
        for maquina, mensaje in casos:
            with self.subTest(maquina=maquina):
                with self.assertRaisesMessage(servicio.ErrorGrabado, mensaje):
                    self.mandar(grabado, maquina=maquina)
        self.assertFalse(EnvioMaquina.objects.exists())
        self.assertFalse(Maquina.objects.filter(nombre='GIETZ 03').exists())   # no se crea sola

    def test_solo_se_manda_un_grabado_aprobado_del_mismo_proceso(self):
        pendiente = self.registrar()['grabado']     # PENDIENTE_K1
        with self.assertRaises(servicio.TransicionInvalida):
            self.mandar(pendiente)
        embossing = self.aprobado(of='21000', proceso='EMBOSSING')
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'no de STAMPING'):
            self.mandar(embossing)
        self.assertFalse(EnvioMaquina.objects.exists())

    def test_no_se_manda_dos_veces(self):
        grabado = self.aprobado()
        self.mandar(grabado)
        with self.assertRaises(servicio.TransicionInvalida):
            self.mandar(grabado, of='23000')          # el grabado ya está EN_MAQUINA
        otro = self.aprobado(of='21000')
        with self.assertRaisesMessage(servicio.TransicionInvalida, 'ya está en máquina'):
            self.mandar(otro, of='22741')             # la OF ya está en máquina con otro grabado
        self.assertEqual(EnvioMaquina.objects.count(), 1)

    def test_cantidad_de_formula_del_excel_se_redondea(self):
        envio = self.mandar(self.aprobado(), cantidad_formatos=25248.712595685458)
        self.assertEqual(envio.cantidad_formatos, 25249)

    def test_fecha_invalida(self):
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'dd/mm/aaaa'):
            self.mandar(self.aprobado(), fecha_programada='2026-10-15')


class PlaniRecogerTests(BasePlani):

    def setUp(self):
        self.grabado = self.aprobado()
        self.envio = self.mandar(self.grabado)

    def test_recoger_ok_suma_un_uso_y_vuelve_a_aprobado(self):
        self.recoger(self.envio, ubicacion='Cajón A1', comentario='Todo bien')
        self.envio.refresh_from_db()
        self.grabado.refresh_from_db()
        self.assertEqual((self.grabado.estado, self.grabado.usos_acumulados, self.grabado.ubicacion),
                         ('APROBADO', 1, 'Cajón A1'))
        self.assertEqual((self.envio.estado_fisico, self.envio.ubicacion, self.envio.recogido_por),
                         ('OK', 'Cajón A1', self.operario))
        self.assertIsNotNone(self.envio.recogido_el)

    def test_recoger_repetir_exige_comentario(self):
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'explica el motivo'):
            self.recoger(self.envio, estado='REPETIR', comentario='mal')
        self.recoger(self.envio, estado='REPETIR', comentario='Relieve gastado')
        self.grabado.refresh_from_db()
        self.assertEqual((self.grabado.estado, self.grabado.usos_acumulados), ('REPETIR', 0))

    def test_ubicacion_y_estado_obligatorios(self):
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'ubicación'):
            self.recoger(self.envio, ubicacion='  ')
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'OK o REPETIR'):
            self.recoger(self.envio, estado='BUENO')

    def test_el_flujo_nuevo_no_guarda_fotos(self):
        self.assertNotIn('foto_dano', {campo.name for campo in EnvioMaquina._meta.get_fields()})
        # OrdenFabricacion conserva sus fotos como histórico.
        self.assertIn('foto_dano', {campo.name for campo in OrdenFabricacion._meta.get_fields()})

    def test_envio_de_otra_of_o_ya_recogido(self):
        with self.assertRaisesMessage(servicio.TransicionInvalida, 'no de la OF 23000'):
            self.recoger(self.envio, of='23000')
        self.recoger(self.envio)
        with self.assertRaisesMessage(servicio.TransicionInvalida, 'ya se recogió'):
            self.recoger(self.envio)
        self.grabado.refresh_from_db()
        self.assertEqual(self.grabado.usos_acumulados, 1)   # no se sumó dos veces



class PlaniVistasTests(BasePlani):

    def setUp(self):
        self.client.force_login(self.operario)

    def crear_excel(self):
        carpeta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, carpeta, ignore_errors=True)
        ruta = os.path.join(carpeta, 'plani.xlsx')
        hoja = pd.DataFrame([
            {'ORDEN': 22741, 'FECHA STAMPING': pd.Timestamp('2026-10-15'), 'REFERENCIA': 'Anilla dorada',
             'CLIENTE': 'CLIENTE SA', 'HORAS PROCESO': 2, 'PAPEL': 'Couché', 'CANTIDAD  FORMATOS': 1200,
             'RESPONSABLE': 'GIETZ 01'},
            {'ORDEN': 23000, 'FECHA STAMPING': pd.Timestamp('2026-10-16'), 'REFERENCIA': 'Otra\r\nanilla',
             'CLIENTE': 'CLIENTE SA\r', 'HORAS PROCESO': 1, 'PAPEL': 'Couché', 'CANTIDAD  FORMATOS': 800,
             'RESPONSABLE': 'GIETZ 01'},
        ])
        with pd.ExcelWriter(ruta, engine='openpyxl') as escritor:
            hoja.to_excel(escritor, sheet_name='STAMPING', index=False)
        return ruta

    def test_sincronizar_agrega_el_estado_del_grabado_y_no_escribe(self):
        grabado = self.aprobado()
        externos = {22741: info_externa(of_stamping='22741'), 23000: info_externa(of_stamping='22741')}
        antes = (Grabado.objects.count(), EnvioMaquina.objects.count(), OrdenFabricacion.objects.count(),
                 FabricacionGrabado.objects.count())
        with override_settings(PLANI_EXCEL_PATH=self.crear_excel()), \
                mock.patch('apps.gestion_grabados.views.buscar_datos_externos_batch', return_value=externos):
            respuesta = self.client.get(reverse('grabados:api_sincronizar'))
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        filas = {f['of']: f for f in respuesta.json()['data']}
        self.assertEqual(filas['22741']['grabado']['accion'], selectors.PLANI_MANDAR)
        self.assertEqual(filas['22741']['grabado']['grabado']['id'], grabado.id)
        self.assertTrue(filas['23000']['grabado']['grabado']['usa_grabado_de_otra'])
        # Datos de la fila que se copian al envío (cantidad viene como numpy.int64 del Excel).
        self.assertEqual((filas['22741']['cantidad_formatos'], filas['22741']['papel']), (1200, 'Couché'))
        self.assertEqual(filas['22741']['fecha_programada'], '15/10/2026')
        self.assertNotIn('estado_db', filas['22741'])
        # Saltos de línea dentro de las celdas (el _x000D_ del Excel real) limpios.
        self.assertEqual((filas['23000']['cliente'], filas['23000']['descripcion']), ('CLIENTE SA', 'Otra anilla'))
        despues = (Grabado.objects.count(), EnvioMaquina.objects.count(), OrdenFabricacion.objects.count(),
                   FabricacionGrabado.objects.count())
        self.assertEqual(antes, despues)

    def test_mandar_y_recoger_por_api(self):
        grabado = self.aprobado()
        respuesta = self.client.post(
            reverse('grabados:api_plani_mandar'),
            data=json.dumps({'of': '22741', 'proceso': 'STAMPING', 'grabado_id': grabado.id, 'fila': fila_excel()}),
            content_type='application/json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        envio = EnvioMaquina.objects.get(pk=respuesta.json()['envio_id'])

        respuesta = self.client.post(reverse('grabados:api_plani_recoger'), data={
            'envio_id': envio.id, 'of': '22741', 'estado_fisico': 'OK', 'ubicacion': 'Cajón C3', 'comentario': '',
        })
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertIn('Usos acumulados: 1', respuesta.json()['message'])

    def test_errores_por_api(self):
        grabado = self.aprobado()
        respuesta = self.client.post(
            reverse('grabados:api_plani_mandar'),
            data=json.dumps({'of': '22741', 'proceso': 'STAMPING', 'grabado_id': grabado.id,
                             'fila': fila_excel(maquina='GIETZ 03')}),
            content_type='application/json')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('regístrala en el administrador', respuesta.json()['message'])
        respuesta = self.client.post(reverse('grabados:api_plani_recoger'), data={'envio_id': 999999, 'of': '22741',
                                                                                   'estado_fisico': 'OK', 'ubicacion': 'X'})
        self.assertEqual(respuesta.status_code, 400)
        for nombre in ('api_plani_mandar', 'api_plani_recoger'):
            self.assertEqual(self.client.get(reverse(f'grabados:{nombre}')).status_code, 405)

    def test_requieren_login_y_csrf(self):
        grabado = self.aprobado()
        anonimo = Client()
        for nombre in ('api_plani_mandar', 'api_plani_recoger'):
            respuesta = anonimo.post(reverse(f'grabados:{nombre}'))
            self.assertEqual(respuesta.status_code, 302)
        estricto = Client(enforce_csrf_checks=True)
        estricto.force_login(self.operario)
        respuesta = estricto.post(
            reverse('grabados:api_plani_mandar'),
            data=json.dumps({'of': '22741', 'proceso': 'STAMPING', 'grabado_id': grabado.id, 'fila': fila_excel()}),
            content_type='application/json')
        self.assertEqual(respuesta.status_code, 403)
        self.assertFalse(EnvioMaquina.objects.exists())

    def test_pagina_plani_sin_datos_tecnicos(self):
        Maquina.objects.create(nombre='Star  foil')
        respuesta = self.client.get(reverse('grabados:plani_consulta'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'id="modal-mandar"')
        self.assertContains(respuesta, 'id="modal-recoger"')
        self.assertNotContains(respuesta, 'prod-peso-i')            # ya no hay formulario técnico
        self.assertNotContains(respuesta, 'type="file"')            # ni fotos de daño
        self.assertContains(respuesta, '"STAR FOIL"')               # máquinas activas normalizadas
        self.assertNotContains(respuesta, '"GIETZ 99"')             # inactiva

    def test_endpoints_viejos_eliminados(self):
        for ruta in ('/grabados/api/registrar/', '/grabados/api/confirmar/'):
            self.assertEqual(self.client.post(ruta).status_code, 404)


class LimpiarTextoExcelTests(TestCase):

    def test_decodifica_los_caracteres_de_control_de_openpyxl(self):
        casos = {
            'ADV & Mckay Cigars_x000D_': 'ADV & Mckay Cigars',
            'Línea 1_x000D__x000A_Línea 2': 'Línea 1 Línea 2',
            'Tab_x0009_aquí': 'Tab aquí',
            '  espacios   de más\r\n': 'espacios de más',
            'Literal _x005F_x000D_ queda': 'Literal _x000D_ queda',   # "_x" literal escapado por Excel
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(limpiar_texto_excel(entrada), esperado)

    def test_lo_que_no_es_texto_queda_igual(self):
        for valor in (22741, 2.5, None):
            self.assertEqual(limpiar_texto_excel(valor), valor)


class FabricacionRetiradaTests(BaseGrabados):

    def setUp(self):
        self.client.force_login(self.operario)

    def test_pantalla_redirige_a_alta_y_no_esta_en_el_menu(self):
        respuesta = self.client.get(reverse('grabados:fabricacion'))
        self.assertRedirects(respuesta, reverse('grabados:alta_grabado'))
        menu = self.client.get(reverse('grabados:alta_grabado'))
        self.assertNotContains(menu, f'href="{reverse("grabados:fabricacion")}"')

    def test_ya_no_escribe_en_orden_fabricacion(self):
        for nombre, datos in (('api_fabricacion_registrar', {'of': '22741', 'proceso': 'STAMPING'}),
                              ('api_fabricacion_registrar_lote', {'ofs': '22741', 'proceso': 'STAMPING',
                                                                  'estado': 'COMPLETADO'}),
                              ('api_fabricacion_editar_ubicacion', {'of': '22741', 'proceso': 'STAMPING'})):
            with self.subTest(endpoint=nombre):
                respuesta = self.client.post(reverse(f'grabados:{nombre}'), data=json.dumps(datos),
                                             content_type='application/json')
                self.assertEqual(respuesta.status_code, 410)
                self.assertIn('Alta de Grabado', respuesta.json()['message'])
        self.assertFalse(OrdenFabricacion.objects.exists())

    def test_pantallas_legadas_marcadas_como_historico(self):
        for nombre in ('grabado_consulta', 'grabado_estadisticas'):
            with self.subTest(pantalla=nombre):
                self.assertContains(self.client.get(reverse(f'grabados:{nombre}')), '(Histórico)')

    def test_alta_acepta_of_y_proceso_en_la_url(self):
        respuesta = self.client.get(reverse('grabados:alta_grabado') + '?of=22741&proceso=STAMPING')
        self.assertEqual(respuesta.status_code, 200)


# ============================================================
# COMANDO migrar_a_grabados (fase 4)
# ============================================================

RUTA_CONSULTAR_EXTERNOS = 'apps.gestion_grabados.management.commands.migrar_a_grabados.consultar_externos'
ANTES_DEL_CORTE = datetime(2026, 7, 1, 12, 0, tzinfo=dt_timezone.utc)
DESPUES_DEL_CORTE = datetime(2026, 9, 1, 12, 0, tzinfo=dt_timezone.utc)
MODELOS_NUEVOS = (Grabado, FabricacionGrabado, EnvioMaquina, Maquina, LegadoOrden)


class MigrarAGrabadosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.operario = User.objects.create_user('operario', password='x')

    def setUp(self):
        self.salida = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.salida, ignore_errors=True)

    def orden(self, of, estado='COMPLETADO', proceso='STAMPING', referencia=None,
              creado=DESPUES_DEL_CORTE, tecnicos=True, **campos):
        datos = {'descripcion': 'ANILLA LEGADA', 'cliente': 'CLIENTE LEGADO', 'maquina': 'Gietz  01',
                 'usos_acumulados': 1, 'usuario': self.operario, 'ubicacion': 'CAJON A1'}
        if tecnicos:
            datos.update(responsables='Juan', peso_inicial=3000, peso_final=2000, perdida=1000,
                         temp=150, rpm=800, tiempo='5h', compensacion='0.5mm')
        datos.update(campos)
        orden = OrdenFabricacion.objects.create(of=of, referencia=referencia, proceso=proceso,
                                                estado=estado, **datos)
        # creado_el / actualizado_el son automáticos: se fijan después.
        OrdenFabricacion.objects.filter(pk=orden.pk).update(creado_el=creado, actualizado_el=creado)
        return orden

    def migrar(self, *args, externos=None):
        salida = StringIO()
        with mock.patch(RUTA_CONSULTAR_EXTERNOS, return_value=externos or {}):
            call_command('migrar_a_grabados', *args, '--salida', self.salida, stdout=salida)
        return salida.getvalue()

    def assertNadaGuardado(self):
        for modelo in MODELOS_NUEVOS:
            self.assertFalse(modelo.objects.exists(), modelo.__name__)

    def test_sin_aplicar_no_guarda_nada_y_deja_reporte(self):
        self.orden('22741')
        self.orden('22750', estado='EN_MAQUINA')
        salida = self.migrar()

        self.assertIn('SIMULACIÓN: no se guardó nada', salida)
        self.assertIn('CUADRA', salida)
        self.assertNadaGuardado()
        archivos = os.listdir(self.salida)
        self.assertEqual(len(archivos), 1)
        self.assertTrue(archivos[0].startswith('migrar_a_grabados_'))

    def test_agrupa_por_referencia_y_toma_estado_de_la_fila_mas_reciente(self):
        primera = self.orden('22741', estado='REPETIR', descripcion='FALLO: borde roto', usos_acumulados=3)
        segunda = self.orden('22900', referencia='22741', creado=DESPUES_DEL_CORTE + timedelta(days=2),
                             usos_acumulados=4)
        pendiente = self.orden('22950', referencia='22741', estado='PENDIENTE',
                               creado=DESPUES_DEL_CORTE + timedelta(days=5), usos_acumulados=9)
        self.migrar('--aplicar')

        grabado = Grabado.objects.get()
        self.assertEqual((grabado.of_origen, grabado.proceso, grabado.estado), ('22741', 'STAMPING', 'APROBADO'))
        self.assertTrue(grabado.aprobado_legado)
        self.assertFalse(grabado.pruebas_k1.exists())
        self.assertEqual(grabado.usos_acumulados, 7)  # el PENDIENTE no cuenta

        self.assertEqual(list(grabado.fabricaciones.values_list('numero', 'tipo')),
                         [(1, 'INICIAL'), (2, 'REPETICION')])
        inicial = grabado.fabricaciones.get(numero=1)
        self.assertEqual((inicial.tiempo, inicial.compensacion), ('5h', '0.5mm'))
        self.assertEqual(inicial.registrado_por, self.operario)
        self.assertFalse(inicial.revisar_pesos)

        envio = primera.legado.envio
        self.assertEqual((envio.estado_fisico, envio.comentario), ('REPETIR', 'FALLO: borde roto'))
        self.assertIsNotNone(envio.recogido_el)
        self.assertEqual(envio.maquina.nombre, 'GIETZ 01')
        self.assertEqual(segunda.legado.envio.estado_fisico, 'OK')

        self.assertEqual((primera.legado.rol, segunda.legado.rol), ('FABRICACION', 'FABRICACION'))
        self.assertEqual(pendiente.legado.rol, 'SIN_MIGRAR')
        self.assertEqual(pendiente.legado.grabado, grabado)
        # La API no lo encontró: datos de las filas, sin la descripción "FALLO:".
        self.assertEqual(grabado.referencia, 'ANILLA LEGADA')
        self.assertTrue(grabado.datos_manuales)

    def test_en_maquina_queda_con_envio_abierto_y_maquina_comodin(self):
        orden = self.orden('22741', estado='EN_MAQUINA', maquina=None)
        self.migrar('--aplicar')

        self.assertEqual(Grabado.objects.get().estado, 'EN_MAQUINA')
        self.assertEqual(orden.legado.rol, 'FABRICACION')
        envio = orden.legado.envio
        self.assertIsNone(envio.recogido_el)
        self.assertEqual(envio.maquina.nombre, 'SIN MÁQUINA (LEGADO)')
        self.assertFalse(envio.maquina.activa)

    def test_en_maquina_sin_datos_tecnicos_es_envio_abierto(self):
        orden = self.orden('22741', estado='EN_MAQUINA', tecnicos=False)
        self.migrar('--aplicar')
        self.assertEqual(orden.legado.rol, 'ENVIO_ABIERTO')
        self.assertIsNone(orden.legado.envio.recogido_el)

    def test_revision_pasa_a_repetir_y_en_proceso_a_aprobado_sin_envio(self):
        revision = self.orden('22741', estado='REVISION')
        en_proceso = self.orden('22742', estado='EN_PROCESO', tecnicos=False)
        self.migrar('--aplicar')

        self.assertEqual(Grabado.objects.get(of_origen='22741').estado, 'REPETIR')
        self.assertEqual(Grabado.objects.get(of_origen='22742').estado, 'APROBADO')
        self.assertEqual(revision.legado.envio.estado_fisico, 'REPETIR')
        self.assertEqual(en_proceso.legado.rol, 'USO')
        self.assertIsNone(en_proceso.legado.envio)

    def test_datos_de_la_api_y_pesos_anteriores_al_corte(self):
        self.orden('22741', creado=ANTES_DEL_CORTE)
        externos = {'22741': info_externa(cliente='CLIENTE API', referencia='REF API', sobre='S-1')}
        salida = self.migrar('--aplicar', externos=externos)

        grabado = Grabado.objects.get()
        self.assertEqual((grabado.cliente, grabado.referencia, grabado.sobre),
                         ('CLIENTE API', 'REF API', 'S-1'))
        self.assertFalse(grabado.datos_manuales)
        fabricacion = grabado.fabricaciones.get()
        self.assertTrue(fabricacion.revisar_pesos)
        self.assertEqual(fabricacion.peso_inicial, 3000)  # sin convertir
        self.assertIn('PESOS ANTERIORES', salida)

    def test_unifica_maquinas_por_forma_normalizada(self):
        Maquina.objects.create(nombre='GIETZ 01')
        self.orden('22741', maquina='gietz 01')
        self.orden('22742', maquina=' Gietz   01 ')
        self.migrar('--aplicar')
        self.assertEqual(list(Maquina.objects.values_list('nombre', flat=True)), ['GIETZ 01'])

    def test_es_idempotente(self):
        self.orden('22741')
        self.orden('22742', estado='PENDIENTE')
        self.migrar('--aplicar')
        conteos = [modelo.objects.count() for modelo in MODELOS_NUEVOS]

        salida = self.migrar('--aplicar')
        self.assertEqual([modelo.objects.count() for modelo in MODELOS_NUEVOS], conteos)
        self.assertIn('CUADRA', salida)

    def test_reutiliza_grabado_de_alta_y_reporta_conflicto(self):
        existente = Grabado.objects.create(of_origen='22741', proceso='STAMPING', cliente='ALTA',
                                           referencia='ALTA', estado='PENDIENTE_K1', usos_acumulados=2)
        self.orden('22741', usos_acumulados=5)
        salida = self.migrar('--aplicar')

        self.assertEqual(Grabado.objects.count(), 1)
        existente.refresh_from_db()
        self.assertEqual((existente.estado, existente.aprobado_legado, existente.cliente),
                         ('PENDIENTE_K1', False, 'ALTA'))
        self.assertEqual(existente.usos_acumulados, 7)
        self.assertIn('el grabado de Alta está en PENDIENTE_K1', salida)

    def test_conciliacion_que_no_cuadra_revierte_todo(self):
        self.orden('22741')
        # 1.ª llamada: alcance; 2.ª: conciliación al final de la transacción.
        with mock.patch.object(OrdenFabricacion.objects, 'count', side_effect=[1, 99]):
            with self.assertRaises(CommandError) as error:
                self.migrar('--aplicar')
        self.assertIn('no cuadra', str(error.exception))
        self.assertNadaGuardado()

    def test_api_caida_detiene_sin_guardar(self):
        self.orden('22741')
        with mock.patch(RUTA_CONSULTAR_EXTERNOS, side_effect=ConnectionError('sin respuesta')):
            with self.assertRaises(CommandError) as error:
                call_command('migrar_a_grabados', '--aplicar', '--salida', self.salida, stdout=StringIO())
        self.assertIn('La API externa no respondió', str(error.exception))
        self.assertNadaGuardado()

    def test_no_modifica_orden_fabricacion(self):
        orden = self.orden('22741', estado='REPETIR')
        antes = OrdenFabricacion.objects.values().get(pk=orden.pk)
        self.migrar('--aplicar')
        self.assertEqual(OrdenFabricacion.objects.values().get(pk=orden.pk), antes)
