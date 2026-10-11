"""
Pruebas de los modelos nuevos: Crear Grabado (K1 o directo) y decisión de K1 (fase 2),
inventario, y PLANI con envío a máquina y recogida (fase 3), y el comando
migrar_a_grabados (fase 4).

Los datos externos (API / externa_2012) se simulan con mock: las pruebas solo
usan la base `default` (test_CigarRingsEIS), que Django crea y borra sola.
"""
import json
import os
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone as dt_timezone
from io import StringIO
from pathlib import Path
from unittest import mock

import pandas as pd
from django.contrib import admin
from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import F
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.gestion_grabados import selectors
from apps.gestion_grabados.models import (
    EnvioMaquina, EstadoBano, FabricacionGrabado, Grabado, HistorialActivacion, HistorialUbicacion,
    LegadoOrden, Maquina, OrdenFabricacion, PruebaK1,
)
from apps.gestion_grabados.services import grabados as servicio
from apps.gestion_grabados.views import VACIO_INFO_EXTERNA, _normalizar_of, limpiar_texto_excel

GRUPO_GRABADOS = 'Grabados'
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
        # operario y supervisor: usuarios comunes con acceso al módulo (grupo Grabados).
        grupo = Group.objects.get(name=GRUPO_GRABADOS)
        cls.operario = User.objects.create_user('operario', password='x')
        cls.supervisor = User.objects.create_user('supervisor', password='x')
        cls.operario.groups.add(grupo)
        cls.supervisor.groups.add(grupo)
        cls.sin_acceso = User.objects.create_user('sin_acceso', password='x')
        cls.admin = User.objects.create_superuser('admin', password='x')

    def registrar(self, of='22741', proceso='STAMPING', info=None, usuario=None,
                  maquina_id=None, datos_manuales=None, tipo='K1', **cambios_tecnicos):
        return servicio.registrar_fabricacion(
            of_origen=of, proceso=proceso,
            info_externa=info if info is not None else info_externa(),
            normalizar_of=_normalizar_of,
            datos_tecnicos=tecnicos(**cambios_tecnicos),
            maquina_id=maquina_id if maquina_id is not None else self.maquina.id,
            datos_manuales=datos_manuales or {},
            usuario=usuario or self.operario,
            tipo=tipo,
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
        self.assertEqual(grabado.tipo, 'K1')
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

    def test_directo_queda_aprobado_sin_k1_ni_maquina(self):
        resultado = self.registrar(tipo='DIRECTO', maquina_id='')

        grabado = Grabado.objects.get()
        self.assertEqual(resultado['accion'], servicio.ACCION_ALTA)
        self.assertEqual((grabado.tipo, grabado.estado), ('DIRECTO', 'APROBADO'))
        self.assertIsNone(resultado['prueba_k1'])
        self.assertFalse(PruebaK1.objects.exists())
        fabricacion = grabado.fabricaciones.get()
        self.assertEqual((fabricacion.numero, fabricacion.tipo), (1, 'INICIAL'))
        self.assertEqual((fabricacion.perdida, fabricacion.temp, fabricacion.rpm), (1000, 150, 800))
        # El baño se suma igual que en un K1.
        self.assertAlmostEqual(EstadoBano.obtener().ml_acumulados, 6.6)

    def test_directo_ignora_la_maquina_aunque_llegue(self):
        self.registrar(tipo='DIRECTO', maquina_id=self.maquina_inactiva.id)
        self.assertEqual(Grabado.objects.get().estado, 'APROBADO')
        self.assertFalse(PruebaK1.objects.exists())

    def test_alta_exige_tipo_valido(self):
        for tipo in (None, '', 'LEGADO', 'OTRO'):
            with self.subTest(tipo=tipo):
                with self.assertRaisesMessage(servicio.ErrorGrabado, 'tipo de grabado'):
                    self.registrar(tipo=tipo)
        self.assertFalse(Grabado.objects.exists())

    def test_rechazo_k1_ignora_el_tipo_enviado(self):
        primera = self.registrar()
        servicio.decidir_k1(prueba_id=primera['prueba_k1'].id, usuario=self.supervisor,
                            aprobar=False, motivo='Relieve incompleto')
        resultado = self.registrar(tipo='DIRECTO')
        grabado = Grabado.objects.get()
        self.assertEqual(resultado['accion'], servicio.ACCION_RECHAZO_K1)
        self.assertEqual((grabado.tipo, grabado.estado), ('K1', 'PENDIENTE_K1'))
        self.assertEqual(resultado['prueba_k1'].intento, 2)

    def test_directo_en_repetir_se_refabrica_sin_k1(self):
        self.registrar(tipo='DIRECTO', maquina_id='')
        Grabado.objects.update(estado='REPETIR')

        resultado = self.registrar(tipo=None, maquina_id='')
        grabado = Grabado.objects.get()
        self.assertEqual(resultado['accion'], servicio.ACCION_REPETICION)
        self.assertEqual((grabado.tipo, grabado.estado), ('DIRECTO', 'APROBADO'))
        self.assertEqual(list(grabado.fabricaciones.order_by('numero').values_list('tipo', flat=True)),
                         ['INICIAL', 'REPETICION'])
        self.assertFalse(PruebaK1.objects.exists())

    def test_en_fabricacion_solo_abre_k1_en_grabados_k1(self):
        # Un DIRECTO o LEGADO nunca debería quedar EN_FABRICACION; si pasa, se bloquea.
        for tipo in ('DIRECTO', 'LEGADO'):
            with self.subTest(tipo=tipo):
                Grabado.objects.all().delete()
                Grabado.objects.create(of_origen='22741', proceso='STAMPING', cliente='C',
                                       tipo=tipo, estado='EN_FABRICACION')
                evaluacion = servicio.evaluar_alta('22741', 'STAMPING', info_externa(), _normalizar_of)
                self.assertEqual(evaluacion['accion'], servicio.ACCION_BLOQUEADA)
                self.assertEqual(evaluacion['bloqueo'], servicio.BLOQUEO_ESTADO)
                self.assertEqual(evaluacion['grabado']['tipo'], tipo)
                with self.assertRaises(servicio.TransicionInvalida):
                    self.registrar()
        self.assertFalse(FabricacionGrabado.objects.exists())

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

    def test_usuario_comun_aprueba_su_propia_fabricacion(self):
        prueba = self.registrar(of='31000', usuario=self.operario)['prueba_k1']
        servicio.decidir_k1(prueba_id=prueba.id, usuario=self.operario, aprobar=True)
        prueba.refresh_from_db()
        self.assertEqual(prueba.resultado, 'APROBADO')
        self.assertEqual(prueba.decidido_por, self.operario)
        self.assertIsNotNone(prueba.decidido_el)
        self.assertTrue(servicio.es_auto_decision(prueba))
        self.assertEqual(prueba.grabado.estado, 'APROBADO')

    def test_usuario_comun_rechaza_su_propia_fabricacion(self):
        prueba = self.registrar(of='31500', usuario=self.operario)['prueba_k1']
        servicio.decidir_k1(prueba_id=prueba.id, usuario=self.operario,
                            aprobar=False, motivo='Rechazo propio')
        prueba.refresh_from_db()
        self.assertEqual((prueba.resultado, prueba.decidido_por), ('RECHAZADO', self.operario))
        self.assertTrue(servicio.es_auto_decision(prueba))
        self.assertEqual(prueba.grabado.estado, 'EN_FABRICACION')

    def test_superusuario_aprueba_su_propia_fabricacion(self):
        prueba = self.registrar(of='32000', usuario=self.admin)['prueba_k1']
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

    def test_pagina_se_llama_crear_grabado_y_pide_el_tipo(self):
        respuesta = self.client.get(reverse('grabados:alta_grabado'))
        self.assertContains(respuesta, 'Crear Grabado | CIGAR RINGS')
        self.assertContains(respuesta, '>Crear Grabado</a')   # menú
        self.assertNotContains(respuesta, 'Alta de Grabado')
        self.assertContains(respuesta, 'name="alta-tipo" value="K1"')
        self.assertContains(respuesta, 'name="alta-tipo" value="DIRECTO"')
        self.assertContains(respuesta, '<span>K1 – Prueba</span>', html=True)
        self.assertContains(respuesta, '<span>Producción</span>', html=True)
        self.assertNotContains(respuesta, 'tipo-opcion__detalle')   # sin textos debajo, solo title

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
            data=json.dumps({'of': '22741', 'proceso': 'STAMPING', 'tipo': 'K1', 'maquina_id': self.maquina.id,
                             'tecnicos': tecnicos()}),
            content_type='application/json',
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual((respuesta.json()['estado'], respuesta.json()['tipo']), ('PENDIENTE_K1', 'K1'))
        self.assertIn('bano', respuesta.json())

    @mock.patch(RUTA_BUSCAR_EXTERNOS, return_value=info_externa())
    def test_registrar_directo_por_api_sin_maquina(self, _mock):
        respuesta = self.client.post(
            reverse('grabados:api_alta_registrar'),
            data=json.dumps({'of': '22741', 'proceso': 'STAMPING', 'tipo': 'directo', 'maquina_id': None,
                             'tecnicos': tecnicos()}),
            content_type='application/json',
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        datos = respuesta.json()
        self.assertEqual((datos['estado'], datos['tipo']), ('APROBADO', 'DIRECTO'))
        self.assertIn('sin K1', datos['message'])
        self.assertIn('bano', datos)

    def payload_directo_33333(self, **cambios):
        """Caso reportado: Producción – directo, OF 33333 STAMPING que no está en
        el sistema externo, datos a mano, pérdida de 50 g y compensación 0."""
        payload = {
            'of': '33333', 'proceso': 'STAMPING', 'tipo': 'DIRECTO', 'maquina_id': None,
            'cliente': 'eugenio', 'referencia': '44444', 'sobre': '',
            'tecnicos': {'responsables': 'Juan', 'tiempo': '32', 'peso_inicial': '800',
                         'peso_final': '750', 'temp': '150', 'rpm': '500',
                         'compensacion': '0', 'compensacion_motivo': ''},
        }
        payload.update(cambios)
        return payload

    def post_registrar(self, payload):
        return self.client.post(reverse('grabados:api_alta_registrar'), data=json.dumps(payload),
                                content_type='application/json')

    @mock.patch(RUTA_BUSCAR_EXTERNOS, return_value=info_externa(encontrado=False))
    def test_registrar_directo_caso_reportado_of_33333(self, _mock):
        respuesta = self.post_registrar(self.payload_directo_33333())
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual((respuesta.json()['estado'], respuesta.json()['tipo']), ('APROBADO', 'DIRECTO'))

        grabado = Grabado.objects.get()
        self.assertEqual((grabado.of_origen, grabado.cliente, grabado.referencia, grabado.sobre),
                         ('33333', 'eugenio', '44444', None))
        self.assertTrue(grabado.datos_manuales)
        self.assertFalse(PruebaK1.objects.exists())
        fabricacion = grabado.fabricaciones.get()
        self.assertEqual((fabricacion.perdida, fabricacion.rpm, fabricacion.tiempo), (50, 500, '32'))
        self.assertAlmostEqual(fabricacion.bano_ml, 0.33)
        self.assertAlmostEqual(respuesta.json()['bano']['ml_acumulados'], 0.3)

    @mock.patch(RUTA_BUSCAR_EXTERNOS, return_value=info_externa(encontrado=False))
    def test_registrar_directo_sin_tipo_da_400_con_mensaje(self, _mock):
        # Lo que mandaba la versión anterior del JS (cacheada en el navegador): sin 'tipo'.
        payload = self.payload_directo_33333()
        del payload['tipo']
        respuesta = self.post_registrar(payload)
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json(),
                         {'status': 'error', 'message': 'Selecciona el tipo de grabado (K1 o Producción).'})
        self.assertFalse(Grabado.objects.exists())

    def test_pagina_pide_el_tipo_antes_de_la_of_y_versiona_el_js(self):
        html = self.client.get(reverse('grabados:alta_grabado')).content.decode()
        self.assertLess(html.index('name="alta-tipo"'), html.index('id="alta-of"'))
        self.assertIn('id="alta-aviso-guardado"', html)
        # ?v=<fecha de modificación>: el navegador no reutiliza un JS viejo de su caché.
        self.assertRegex(html, r'js/alta_grabado\.js\?v=\d+')

    @mock.patch(RUTA_BUSCAR_EXTERNOS, return_value=info_externa())
    def test_registrar_por_api_sin_tipo_falla(self, _mock):
        respuesta = self.client.post(
            reverse('grabados:api_alta_registrar'),
            data=json.dumps({'of': '22741', 'proceso': 'STAMPING', 'maquina_id': self.maquina.id,
                             'tecnicos': tecnicos()}),
            content_type='application/json',
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('tipo de grabado', respuesta.json()['message'])
        self.assertFalse(Grabado.objects.exists())

    @mock.patch(RUTA_BUSCAR_EXTERNOS, return_value=info_externa())
    def test_registrar_por_api_con_error_de_validacion(self, _mock):
        respuesta = self.client.post(
            reverse('grabados:api_alta_registrar'),
            data=json.dumps({'of': '22741', 'proceso': 'STAMPING', 'tipo': 'K1', 'maquina_id': self.maquina.id,
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

    def test_sin_acceso_al_modulo_no_entra(self):
        self.client.force_login(self.sin_acceso)
        self.assertEqual(self.client.get(reverse('grabados:k1_pendientes')).status_code, 403)
        self.assertEqual(self.client.get(reverse('grabados:api_k1_pendientes')).status_code, 403)
        respuesta = self.client.post(reverse('grabados:api_k1_aprobar', args=[self.prueba.id]),
                                     data='{}', content_type='application/json')
        self.assertEqual(respuesta.status_code, 403)
        self.prueba.refresh_from_db()
        self.assertEqual(self.prueba.resultado, 'PENDIENTE')

    def test_menu_muestra_k1_a_cualquier_usuario_del_modulo(self):
        self.client.force_login(self.operario)
        self.assertContains(self.client.get(reverse('grabados:alta_grabado')),
                            reverse('grabados:k1_pendientes'))

    def test_supervisor_lista_y_aprueba(self):
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get(reverse('grabados:k1_pendientes')).status_code, 200)

        datos = self.client.get(reverse('grabados:api_k1_pendientes')).json()['data']
        self.assertEqual(len(datos), 1)
        self.assertEqual(datos[0]['of'], '22741')
        self.assertEqual(datos[0]['registrado_por'], 'operario')
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

    def test_usuario_comun_aprueba_su_propio_k1_por_api(self):
        # self.prueba la registró operario: él mismo la aprueba, sin bloqueo.
        self.client.force_login(self.operario)
        datos = self.client.get(reverse('grabados:api_k1_pendientes')).json()['data']
        self.assertNotIn('puede_decidir', datos[0])
        self.assertNotIn('es_propio', datos[0])

        respuesta = self.client.post(reverse('grabados:api_k1_aprobar', args=[self.prueba.id]),
                                     data='{}', content_type='application/json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.prueba.refresh_from_db()
        self.assertEqual((self.prueba.resultado, self.prueba.decidido_por), ('APROBADO', self.operario))
        self.assertIsNotNone(self.prueba.decidido_el)

        # En el detalle queda como "Auto-aprobado".
        detalle = self.client.get(reverse('grabados:api_grabado_detalle', args=[self.prueba.grabado_id]))
        intento = detalle.json()['data']['pruebas_k1'][0]
        self.assertEqual((intento['decidido_por'], intento['auto_decision']), ('operario', True))

    def test_pagina_k1_sin_aviso_de_supervisor(self):
        self.client.force_login(self.operario)
        respuesta = self.client.get(reverse('grabados:k1_pendientes'))
        self.assertNotContains(respuesta, 'modal-aviso-propio')
        self.assertNotContains(respuesta, 'supervisor')

    def test_superusuario_decide_su_propio_k1_por_api(self):
        propia = self.registrar(of='30001', usuario=self.admin)['prueba_k1']
        self.client.force_login(self.admin)

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
        cls.consulta = User.objects.create_user('consulta', password='x')
        cls.consulta.groups.add(Group.objects.get(name=GRUPO_GRABADOS))

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

    def test_cualquier_usuario_del_modulo_lo_ve(self):
        d = self.detalle(self.consulta)
        self.assertEqual(d['of_origen'], '22741')
        self.assertEqual(d['k1_actual']['id'], self.k1_pendiente.id)
        self.assertNotIn('puede_decidir', d['k1_actual'])

    def test_datos_generales(self):
        d = self.detalle(self.supervisor)
        self.assertEqual(d['id'], self.grabado.id)
        self.assertEqual((d['proceso'], d['estado'], d['estado_display']),
                         ('STAMPING', 'PENDIENTE_K1', 'Pendiente de K1'))
        self.assertEqual((d['cliente'], d['referencia'], d['sobre']), ('CLIENTE SA', 'ANILLA DORADA', 'S-10'))
        self.assertFalse(d['datos_manuales'])
        self.assertEqual((d['tipo'], d['tipo_display']), ('K1', 'K1 – de prueba'))
        self.assertNotIn('aprobado_legado', d)
        self.assertEqual(d['creado_por'], 'operario')

    def test_detalle_de_grabado_directo(self):
        directo = self.registrar(of='31000', tipo='DIRECTO', maquina_id='')['grabado']
        self.client.force_login(self.consulta)
        d = self.client.get(self.url(directo.id)).json()['data']
        self.assertEqual((d['tipo'], d['tipo_display'], d['estado']), ('DIRECTO', 'Producción – directo', 'APROBADO'))
        self.assertIsNone(d['k1_actual'])
        self.assertEqual(d['pruebas_k1'], [])
        self.assertEqual(len(d['fabricaciones']), 1)

    def test_k1_actual_con_la_fabricacion_en_prueba(self):
        k1 = self.detalle(self.supervisor)['k1_actual']
        self.assertEqual(k1['id'], self.k1_pendiente.id)
        self.assertEqual((k1['intento'], k1['maquina'], k1['resultado']), (2, 'GIETZ 01', 'PENDIENTE'))
        self.assertEqual(k1['creado_por'], 'operario')
        self.assertEqual(k1['fabricacion']['numero'], 2)
        tecnicos = k1['fabricacion']['tecnicos']
        self.assertEqual((tecnicos['peso_inicial'], tecnicos['peso_final'], tecnicos['perdida']), (5000, 4000, 1000))
        self.assertEqual((tecnicos['temp'], tecnicos['rpm']), (160, 900))
        self.assertAlmostEqual(tecnicos['bano_ml'], 6.6)

    def test_superusuario_autor_decide_y_queda_como_auto_decision(self):
        prueba = self.registrar(of='34000', usuario=self.admin)['prueba_k1']
        self.client.force_login(self.admin)
        url = reverse('grabados:api_grabado_detalle', args=[prueba.grabado_id])

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
        for codigo, _ in Grabado.TIPO_CHOICES:   # filtro por tipo
            self.assertContains(respuesta, f'<option value="{codigo}">')

    def test_conteos_por_estado(self):
        res = self.api()
        self.assertEqual(res['conteos'], {
            'EN_FABRICACION': 0, 'PENDIENTE_K1': 2, 'APROBADO': 1, 'EN_MAQUINA': 0, 'REPETIR': 0,
            'INACTIVO': 0,
        })
        self.assertEqual(res['total'], 3)

    def test_columnas_de_la_fila(self):
        filas = {g['of_origen']: g for g in self.api()['data']}
        g = filas['22741']
        self.assertEqual((g['proceso'], g['cliente'], g['referencia']), ('STAMPING', 'CLIENTE SA', 'ANILLA DORADA'))
        self.assertEqual((g['estado'], g['estado_display']), ('PENDIENTE_K1', 'Pendiente de K1'))
        self.assertEqual((g['tipo'], g['tipo_display']), ('K1', 'K1 – de prueba'))
        self.assertEqual(g['intentos_k1'], 2)
        self.assertEqual((g['ultimo_k1'], g['ultimo_k1_display']), ('PENDIENTE', 'Pendiente'))
        self.assertRegex(g['creado_el'], r'^\d{2}/\d{2}/\d{4}$')

        aprobado = filas['22800']
        self.assertEqual((aprobado['intentos_k1'], aprobado['ultimo_k1']), (1, 'APROBADO'))
        self.assertEqual((aprobado['usos_acumulados'], aprobado['ubicacion']), (4, 'Cajón B2'))
        self.assertIn('id', aprobado)  # para abrir el panel de detalle

    def test_grabado_sin_k1_muestra_cero_intentos(self):
        Grabado.objects.create(of_origen='25000', proceso='STAMPING', cliente='X', estado='APROBADO',
                               tipo='LEGADO')
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

    def test_filtro_por_tipo_afecta_conteos(self):
        self.registrar(of='24000', tipo='DIRECTO', maquina_id='')
        Grabado.objects.create(of_origen='25000', proceso='STAMPING', cliente='X', estado='APROBADO',
                               tipo='LEGADO')
        res = self.api(tipo='DIRECTO')
        self.assertEqual(self.ofs(res), ['24000'])
        self.assertEqual(res['conteos']['APROBADO'], 1)
        self.assertEqual(res['conteos']['PENDIENTE_K1'], 0)
        self.assertEqual(self.ofs(self.api(tipo='legado')), ['25000'])
        self.assertEqual(self.ofs(self.api(tipo='K1')), ['22741', '22800', '22900'])

    def test_filtros_invalidos(self):
        for params in ({'proceso': 'OTRO'}, {'estado': 'PENDIENTE'}, {'tipo': 'OTRO'}):
            with self.subTest(params=params):
                respuesta = self.client.get(reverse('grabados:api_inventario'), params)
                self.assertEqual(respuesta.status_code, 400)

    def test_es_solo_lectura(self):
        respuesta = self.client.post(reverse('grabados:api_inventario'), data='{}', content_type='application/json')
        self.assertEqual(respuesta.status_code, 405)

    def test_limite_de_filas(self):
        self.assertEqual(selectors.LIMITE_INVENTARIO, 500)
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_INVENTARIO', 2):
            res = self.api()
        self.assertEqual(len(res['data']), 2)
        self.assertEqual((res['total'], res['limite']), (3, 2))

    def test_limite_muestra_los_modificados_mas_recientes(self):
        # 22741 es el más viejo; al cambiarle la ubicación entra en el límite.
        viejo = Grabado.objects.get(of_origen='22741')
        servicio.cambiar_ubicacion(grabado_id=viejo.id, ubicacion='GBB-1-1', usuario=self.operario)
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_INVENTARIO', 1):
            res = self.api()
        self.assertEqual(([g['of_origen'] for g in res['data']], res['total']), (['22741'], 3))

    def test_orden_por_ultima_modificacion(self):
        self.assertEqual([g['of_origen'] for g in self.api()['data']], ['22900', '22800', '22741'])
        viejo = Grabado.objects.get(of_origen='22741')
        servicio.cambiar_ubicacion(grabado_id=viejo.id, ubicacion='GBB-1-1', usuario=self.operario)
        filas = self.api()['data']
        self.assertEqual([g['of_origen'] for g in filas], ['22741', '22900', '22800'])
        self.assertRegex(filas[0]['actualizado_el'], r'^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$')

    def test_pagina_con_columnas_nuevas(self):
        respuesta = self.client.get(reverse('grabados:inventario_grabados'))
        for texto in ('Última modificación', 'Ubicación', 'Usos', 'Etiqueta'):
            self.assertContains(respuesta, texto)

    def test_sin_aviso_de_limite_solo_el_contador(self):
        respuesta = self.client.get(reverse('grabados:inventario_grabados'))
        self.assertContains(respuesta, 'id="inv-contador"')
        self.assertNotContains(respuesta, 'inv-aviso-limite')
        js = (Path(__file__).parent / 'static' / 'js' / 'inventario_grabados.js').read_text(encoding='utf-8')
        self.assertNotIn('inv-aviso-limite', js)
        self.assertIn('Mostrando ${res.data.length} de ${res.total}', js)


# ============================================================
# UBICACIÓN: edición en el Inventario y recogida del PLANI, con historial
# ============================================================

class CambiarUbicacionTests(BaseGrabados):

    def setUp(self):
        self.grabado = self.registrar()['grabado']
        self.client.force_login(self.operario)

    def cambiar(self, ubicacion, usuario=None):
        return servicio.cambiar_ubicacion(grabado_id=self.grabado.id, ubicacion=ubicacion,
                                          usuario=usuario or self.operario)

    def api(self, ubicacion, grabado_id=None, cliente=None):
        url = reverse('grabados:api_grabado_ubicacion', kwargs={'grabado_id': grabado_id or self.grabado.id})
        return (cliente or self.client).post(url, data=json.dumps({'ubicacion': ubicacion}),
                                             content_type='application/json')

    def test_guarda_normalizada_y_deja_historial(self):
        antes = Grabado.objects.get(pk=self.grabado.pk).actualizado_el
        grabado = self.cambiar('  gbb - 2 - 1 ')
        self.assertTrue(grabado.ubicacion_cambio)
        self.grabado.refresh_from_db()
        self.assertEqual(self.grabado.ubicacion, 'GBB-2-1')
        self.assertGreater(self.grabado.actualizado_el, antes)
        cambio = HistorialUbicacion.objects.get(grabado=self.grabado)
        self.assertEqual((cambio.ubicacion_anterior, cambio.ubicacion_nueva, cambio.usuario, cambio.origen),
                         (None, 'GBB-2-1', self.operario, 'INVENTARIO'))
        self.assertIsNotNone(cambio.cambiado_el)

        self.cambiar('AB-10-3', usuario=self.supervisor)
        ultimo = HistorialUbicacion.objects.filter(grabado=self.grabado).first()
        self.assertEqual((ultimo.ubicacion_anterior, ultimo.ubicacion_nueva, ultimo.usuario),
                         ('GBB-2-1', 'AB-10-3', self.supervisor))
        self.assertEqual(HistorialUbicacion.objects.count(), 2)

    def test_rechaza_formatos_invalidos(self):
        self.cambiar('GBB-2-1')
        for valor in ('', '   ', 'Cajón B2', 'G-1-1', 'GBBBB-1-1', 'GBB-1', 'GBB-1-1-1', 'GB1-1-1',
                      'GBB-1234-1', 'GBB_1_1', None):
            with self.subTest(valor=valor), self.assertRaises(servicio.ErrorGrabado):
                self.cambiar(valor)
        self.grabado.refresh_from_db()
        self.assertEqual(self.grabado.ubicacion, 'GBB-2-1')
        self.assertEqual(HistorialUbicacion.objects.count(), 1)

    def test_misma_ubicacion_no_escribe(self):
        self.cambiar('GBB-2-1')
        antes = Grabado.objects.get(pk=self.grabado.pk).actualizado_el
        self.assertFalse(self.cambiar('gbb-2-1').ubicacion_cambio)
        self.assertEqual(Grabado.objects.get(pk=self.grabado.pk).actualizado_el, antes)
        self.assertEqual(HistorialUbicacion.objects.count(), 1)

    def test_ubicacion_vieja_se_respeta_hasta_editarla(self):
        Grabado.objects.filter(pk=self.grabado.pk).update(ubicacion='Cajón B2')
        self.cambiar('GBB-2-1')
        cambio = HistorialUbicacion.objects.get()
        self.assertEqual((cambio.ubicacion_anterior, cambio.ubicacion_nueva), ('Cajón B2', 'GBB-2-1'))

    def test_grabado_inexistente(self):
        with self.assertRaises(servicio.GrabadoNoExiste):
            servicio.cambiar_ubicacion(grabado_id=999999, ubicacion='GBB-2-1', usuario=self.operario)

    def test_api_cambia_y_devuelve_ultima_modificacion(self):
        respuesta = self.api('gbb-2-1')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        datos = respuesta.json()
        self.assertEqual((datos['ubicacion'], datos['cambio']), ('GBB-2-1', True))
        self.grabado.refresh_from_db()
        self.assertEqual(datos['actualizado_el'], selectors.formatear_fecha(self.grabado.actualizado_el))
        self.assertEqual(HistorialUbicacion.objects.get().usuario, self.operario)
        self.assertFalse(self.api('GBB-2-1').json()['cambio'])

    def test_api_errores(self):
        respuesta = self.api('Cajón B2')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('GBB-2-1', respuesta.json()['message'])
        self.assertEqual(self.api('GBB-2-1', grabado_id=999999).status_code, 404)
        url = reverse('grabados:api_grabado_ubicacion', kwargs={'grabado_id': self.grabado.id})
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, data='no-json', content_type='application/json').status_code, 400)
        self.assertFalse(HistorialUbicacion.objects.exists())

    def test_api_requiere_permiso_y_csrf(self):
        sin_permiso = Client()
        sin_permiso.force_login(self.sin_acceso)
        self.assertEqual(self.api('GBB-2-1', cliente=sin_permiso).status_code, 403)
        estricto = Client(enforce_csrf_checks=True)
        estricto.force_login(self.operario)
        self.assertEqual(self.api('GBB-2-1', cliente=estricto).status_code, 403)
        self.assertEqual(self.api('GBB-2-1', cliente=Client()).status_code, 302)   # sin sesión
        self.assertFalse(HistorialUbicacion.objects.exists())


class UbicacionAlRecogerTests(BaseGrabados):
    """Segundo camino: Recoger en el PLANI usa el mismo servicio (valida + historial)."""

    def setUp(self):
        resultado = self.registrar()
        servicio.decidir_k1(prueba_id=resultado['prueba_k1'].id, usuario=self.supervisor, aprobar=True)
        self.grabado = resultado['grabado']
        Grabado.objects.filter(pk=self.grabado.pk).update(ubicacion='Cajón A1')   # ubicación vieja
        self.envio = servicio.mandar_a_maquina(
            of='22741', proceso='STAMPING', grabado_id=self.grabado.id, usuario=self.operario,
            fila={'maquina': 'GIETZ 01', 'fecha_programada': '15/10/2026', 'cantidad_formatos': 1200,
                  'horas_proceso': 2.5, 'papel': 'Couché 90 g'})

    def recoger(self, ubicacion, estado='OK', comentario=''):
        return servicio.recoger_de_maquina(envio_id=self.envio.id, of='22741', estado_fisico=estado,
                                           ubicacion=ubicacion, comentario=comentario, usuario=self.operario)

    def test_recoger_valida_normaliza_y_deja_historial(self):
        self.recoger(' gbb-4-2 ')
        self.grabado.refresh_from_db()
        self.envio.refresh_from_db()
        self.assertEqual((self.grabado.ubicacion, self.grabado.estado, self.grabado.usos_acumulados),
                         ('GBB-4-2', 'APROBADO', 1))
        self.assertEqual(self.envio.ubicacion, 'GBB-4-2')
        cambio = HistorialUbicacion.objects.get(grabado=self.grabado)
        self.assertEqual((cambio.ubicacion_anterior, cambio.ubicacion_nueva, cambio.usuario, cambio.origen),
                         ('Cajón A1', 'GBB-4-2', self.operario, 'RECOGIDA'))

    def test_recoger_con_ubicacion_invalida_no_cierra_el_envio(self):
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'no es válida'):
            self.recoger('Cajón A1')
        self.envio.refresh_from_db()
        self.grabado.refresh_from_db()
        self.assertIsNone(self.envio.recogido_el)
        self.assertEqual((self.grabado.estado, self.grabado.ubicacion), ('EN_MAQUINA', 'Cajón A1'))
        self.assertFalse(HistorialUbicacion.objects.exists())

    def test_recoger_en_la_misma_ubicacion_no_duplica_historial(self):
        servicio.cambiar_ubicacion(grabado_id=self.grabado.id, ubicacion='GBB-4-2', usuario=self.operario)
        self.recoger('GBB-4-2', estado='REPETIR', comentario='Relieve gastado')
        self.grabado.refresh_from_db()
        self.assertEqual((self.grabado.estado, self.grabado.ubicacion), ('REPETIR', 'GBB-4-2'))
        self.assertEqual(HistorialUbicacion.objects.count(), 1)

    def test_recoger_por_api(self):
        self.client.force_login(self.operario)
        url = reverse('grabados:api_plani_recoger')
        datos = {'envio_id': self.envio.id, 'of': '22741', 'estado_fisico': 'OK'}
        respuesta = self.client.post(url, data={**datos, 'ubicacion': 'Cajón A1'})
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('GBB-2-1', respuesta.json()['message'])
        respuesta = self.client.post(url, data={**datos, 'ubicacion': 'gbb-5-5'})
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        cambio = HistorialUbicacion.objects.get()
        self.assertEqual((cambio.ubicacion_nueva, cambio.origen, cambio.usuario), ('GBB-5-5', 'RECOGIDA', self.operario))


class ConsultaHistoricaGrabadoTests(BaseGrabados):
    """Tabla de Consultas: grabado enlazado a cada OrdenFabricacion (LegadoOrden ->
    EnvioMaquina -> OF de origen), edición de su ubicación, panel de detalle con
    el historial de ubicaciones, admin y modal Recoger."""

    def setUp(self):
        resultado = self.registrar(of='22741')
        servicio.decidir_k1(prueba_id=resultado['prueba_k1'].id, usuario=self.supervisor, aprobar=True)
        self.grabado = resultado['grabado']
        self.client.force_login(self.operario)

    def orden(self, of, referencia=None, proceso='STAMPING', **campos):
        datos = {'descripcion': 'ANILLA', 'cliente': 'CLIENTE SA', 'estado': 'COMPLETADO',
                 'ubicacion': 'Cajón viejo'}
        datos.update(campos)
        return OrdenFabricacion.objects.create(of=of, referencia=referencia, proceso=proceso, **datos)

    def enlaces(self):
        filas = list(OrdenFabricacion.objects.values(
            'id', 'of', 'referencia', 'proceso',
            legado_grabado=F('legado__grabado_id'), legado_rol=F('legado__rol')))
        return selectors.grabados_de_ordenes(filas)

    def registros(self):
        respuesta = self.client.get(reverse('grabados:api_registros'))
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return {r['id']: r for r in respuesta.json()}

    def editar(self, ubicacion, origen='CONSULTA', grabado_id=None):
        url = reverse('grabados:api_grabado_ubicacion', kwargs={'grabado_id': grabado_id or self.grabado.id})
        return self.client.post(url, data=json.dumps({'ubicacion': ubicacion, 'origen': origen}),
                                content_type='application/json')

    # ------------------------------------------------------------ enlace
    def test_enlace_por_legado_tiene_prioridad(self):
        otro = self.registrar(of='23000', tipo='DIRECTO')['grabado']
        orden = self.orden('22741')     # por OF iría a self.grabado, pero LegadoOrden manda
        LegadoOrden.objects.create(orden=orden, rol='USO', grabado=otro)
        enlace = self.enlaces()[orden.id]
        self.assertEqual((enlace['grabado']['id'], enlace['enlace']), (otro.id, selectors.ENLACE_LEGADO))

    def test_enlace_por_envio_a_maquina(self):
        servicio.mandar_a_maquina(of='30000', proceso='STAMPING', grabado_id=self.grabado.id,
                                  usuario=self.operario, fila={'maquina': 'GIETZ 01'})
        orden = self.orden('30000.0')
        enlace = self.enlaces()[orden.id]
        self.assertEqual((enlace['grabado']['id'], enlace['enlace']), (self.grabado.id, selectors.ENLACE_ENVIO))

    def test_enlace_por_of_referencia_o_propia_of(self):
        por_referencia = self.orden('23000', referencia='22741')
        propia = self.orden('22741', referencia='22741')
        sin_referencia = self.orden('22741-A', referencia='—')   # (of, proceso) es único
        enlaces = self.enlaces()
        for orden in (por_referencia, propia, sin_referencia):
            with self.subTest(orden=orden.of):
                self.assertEqual((enlaces[orden.id]['grabado']['id'], enlaces[orden.id]['enlace']),
                                 (self.grabado.id, selectors.ENLACE_OF))

    def test_legado_sin_grabado_sigue_la_cascada(self):
        orden = self.orden('22741')
        LegadoOrden.objects.create(orden=orden, rol='SIN_MIGRAR', grabado=None)
        self.assertEqual(self.enlaces()[orden.id]['grabado']['id'], self.grabado.id)

    def test_sin_grabado_con_motivo(self):
        otro_proceso = self.orden('22741', proceso='EMBOSSING')
        sin_grabado = self.orden('40000')
        sin_migrar = self.orden('40001')
        LegadoOrden.objects.create(orden=sin_migrar, rol='SIN_MIGRAR', grabado=None)
        invalida = self.orden('SIN NUMERO')
        enlaces = self.enlaces()
        esperado = {otro_proceso.id: selectors.MOTIVO_SIN_GRABADO, sin_grabado.id: selectors.MOTIVO_SIN_GRABADO,
                    sin_migrar.id: selectors.MOTIVO_SIN_MIGRAR, invalida.id: selectors.MOTIVO_OF_INVALIDA}
        for orden_id, motivo in esperado.items():
            with self.subTest(orden_id=orden_id):
                self.assertEqual((enlaces[orden_id]['grabado'], enlaces[orden_id]['motivo']), (None, motivo))

    def test_enlace_en_pocas_consultas(self):
        for i in range(30):
            self.orden(str(23000 + i), referencia='22741')
        filas = list(OrdenFabricacion.objects.values('id', 'of', 'referencia', 'proceso'))
        with self.assertNumQueries(2):     # grabados + envíos, sin importar cuántas filas
            enlaces = selectors.grabados_de_ordenes(filas)
        self.assertTrue(all(e['grabado'] for e in enlaces.values()))

    # ------------------------------------------------ api_registros y edición
    def test_registros_muestran_la_ubicacion_del_grabado(self):
        servicio.cambiar_ubicacion(grabado_id=self.grabado.id, ubicacion='GBB-2-1', usuario=self.operario)
        enlazada = self.orden('23000', referencia='22741')
        suelta = self.orden('40000')
        registros = self.registros()
        self.grabado.refresh_from_db()

        fila = registros[enlazada.id]
        self.assertEqual((fila['ubicacion'], fila['grabado']['id'], fila['enlace'], fila['motivo_sin_grabado']),
                         ('GBB-2-1', self.grabado.id, 'OF', None))
        self.assertEqual(fila['grabado_mod'], selectors.formatear_fecha(self.grabado.actualizado_el))

        fila = registros[suelta.id]
        self.assertEqual((fila['ubicacion'], fila['grabado'], fila['grabado_mod']), ('Cajón viejo', None, None))
        self.assertEqual(fila['motivo_sin_grabado'], selectors.MOTIVO_SIN_GRABADO)

    def test_editar_desde_la_consulta_deja_historial_con_origen_consulta(self):
        orden = self.orden('23000', referencia='22741')
        respuesta = self.editar(' gbb - 3 - 7 ')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.json()['ubicacion'], 'GBB-3-7')
        cambio = HistorialUbicacion.objects.get()
        self.assertEqual((cambio.ubicacion_nueva, cambio.origen, cambio.usuario),
                         ('GBB-3-7', 'CONSULTA', self.operario))
        fila = self.registros()[orden.id]
        self.assertEqual((fila['ubicacion'], fila['grabado_mod']), ('GBB-3-7', respuesta.json()['actualizado_el']))
        # Fila vieja de OrdenFabricacion: no se toca.
        orden.refresh_from_db()
        self.assertEqual(orden.ubicacion, 'Cajón viejo')

    def test_editar_con_formato_u_origen_invalido(self):
        self.assertEqual(self.editar('Cajón B2').status_code, 400)
        respuesta = self.editar('GBB-2-1', origen='RECOGIDA')   # solo lo pone recoger_de_maquina
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()['message'], 'Origen inválido.')
        self.assertFalse(HistorialUbicacion.objects.exists())

    # --------------------------------------------------- panel de detalle
    def test_detalle_incluye_historial_de_ubicaciones(self):
        Grabado.objects.filter(pk=self.grabado.pk).update(ubicacion='Cajón B2')
        self.editar('GBB-2-1', origen='INVENTARIO')
        self.client.force_login(self.supervisor)
        self.editar('GBB-2-2', origen='CONSULTA')

        respuesta = self.client.get(reverse('grabados:api_grabado_detalle',
                                            kwargs={'grabado_id': self.grabado.id}))
        historial = respuesta.json()['data']['historial_ubicaciones']
        self.assertEqual([(c['anterior'], c['nueva'], c['usuario'], c['origen'], c['origen_display'])
                          for c in historial],
                         [('GBB-2-1', 'GBB-2-2', 'supervisor', 'CONSULTA', 'Edición en la Tabla de Consultas'),
                          ('Cajón B2', 'GBB-2-1', 'operario', 'INVENTARIO', 'Edición en el Inventario')])
        self.assertTrue(all(c['cambiado_el'] for c in historial))

    def test_detalle_sin_cambios_de_ubicacion(self):
        self.assertEqual(selectors.detalle_grabado(self.grabado.id)['historial_ubicaciones'], [])

    def test_pagina_de_consulta_usa_el_panel(self):
        html = self.client.get(reverse('grabados:grabado_consulta')).content.decode()
        self.assertIn('panel_grabado.js', html)
        self.assertNotIn('modal-historial', html)

    # ------------------------------------------------- admin y modal Recoger
    def test_admin_ubicacion_de_solo_lectura(self):
        self.client.force_login(self.admin)
        url = reverse('admin:gestion_grabados_grabado_change', args=[self.grabado.id])
        html = self.client.get(url).content.decode()
        self.assertNotIn('name="ubicacion"', html)
        self.assertIn('ubicacion', admin.site._registry[Grabado].get_readonly_fields(None))

    def test_plani_pasa_el_formato_de_ubicacion_al_modal_recoger(self):
        respuesta = self.client.get(reverse('grabados:plani_consulta'))
        self.assertEqual(respuesta.context['patron_ubicacion'], servicio.PATRON_UBICACION.pattern)
        self.assertIn('data-patron="{}"'.format(servicio.PATRON_UBICACION.pattern), respuesta.content.decode())


class ConsultaLimiteYFiltrosTests(BaseGrabados):
    """Tabla de Consultas: sin filtro de fecha por defecto, las LIMITE_CONSULTA filas más
    recientes (fecha de registro, luego id) y búsqueda/filtros sobre todo el historial."""

    def setUp(self):
        self.client.force_login(self.operario)

    def orden(self, of, registro, proceso='STAMPING', **campos):
        datos = {'descripcion': 'ANILLA', 'cliente': 'CLIENTE SA', 'estado': 'COMPLETADO',
                 'fecha_registro': registro}
        datos.update(campos)
        return OrdenFabricacion.objects.create(of=of, proceso=proceso, **datos)

    def consulta(self, q='', filtros=None, esperado=200):
        params = {'q': q}
        if filtros is not None:
            params['filtros'] = json.dumps(filtros)
        respuesta = self.client.get(reverse('grabados:api_consulta_registros'), params)
        self.assertEqual(respuesta.status_code, esperado, respuesta.content)
        return respuesta.json()

    def ofs(self, respuesta):
        return [r['of'] for r in respuesta['data']]

    def test_por_defecto_las_mas_recientes_con_tope(self):
        self.assertEqual(selectors.LIMITE_CONSULTA, 500)
        self.orden('100', date(2024, 1, 5))
        self.orden('101', None)                      # sin fecha de registro: al final
        self.orden('102', date(2026, 9, 1))
        self.orden('103', date(2026, 9, 1))          # misma fecha: gana el id más alto
        self.orden('104', date(2025, 3, 1))
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_CONSULTA', 3):
            res = self.consulta()
        self.assertEqual(self.ofs(res), ['103', '102', '104'])
        self.assertEqual((res['total'], res['limite']), (5, 3))

    def test_of_vieja_aparece_al_buscarla(self):
        vieja = self.orden('10567', date(2019, 2, 1), proceso='EMBOSSING', fecha_programada=None)
        por_referencia = self.orden('22464', date(2019, 1, 1), referencia='10567.0')
        for i in range(3):
            self.orden(str(30000 + i), date(2026, 10, 1))
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_CONSULTA', 2):
            self.assertNotIn('10567', self.ofs(self.consulta()))
            res = self.consulta(q='10567')
        self.assertEqual(([r['id'] for r in res['data']], res['total']), ([vieja.id, por_referencia.id], 2))

    def test_busqueda_con_tope(self):
        for i in range(4):
            self.orden(str(40000 + i), date(2026, 1, 1 + i))
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_CONSULTA', 2):
            res = self.consulta(q='4000')
        self.assertEqual((self.ofs(res), res['total']), (['40003', '40002'], 4))

    def test_filtro_por_columna_mira_todo_el_historial(self):
        self.orden('10567', date(2019, 2, 1), cliente='VIEJO SA')
        self.orden('20000', date(2026, 10, 1), cliente='NUEVO SA', maquina='')
        self.orden('20001', date(2026, 10, 2), cliente='NUEVO SA', maquina='GIETZ 01')
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_CONSULTA', 1):
            res = self.consulta(filtros=[{'col': 'cliente', 'in': ['VIEJO SA']}])
            self.assertEqual((self.ofs(res), res['total']), (['10567'], 1))
        res = self.consulta(filtros=[{'col': 'cliente', 'fuera': ['NUEVO SA']}])
        self.assertEqual(self.ofs(res), ['10567'])
        # '—' = vacío o null; varios filtros se cumplen todos (aunque repitan columna).
        res = self.consulta(filtros=[{'col': 'maquina', 'in': ['—']}, {'col': 'cliente', 'in': ['NUEVO SA']}])
        self.assertEqual(self.ofs(res), ['20000'])
        res = self.consulta(filtros=[{'col': 'estado', 'in': ['COMPLETADO']}, {'col': 'estado', 'in': ['REPETIR']}])
        self.assertEqual(res['total'], 0)

    def test_filtro_numerico_y_por_fecha(self):
        self.orden('50000', date(2026, 1, 1), temp=150.0, fecha_programada=date(2026, 1, 10))
        self.orden('50001', date(2026, 1, 2), temp=150.5)
        self.orden('50002', date(2026, 1, 3))
        res = self.consulta(filtros=[{'col': 'temp', 'in': ['150', '—']}])
        self.assertEqual(self.ofs(res), ['50002', '50000'])
        res = self.consulta(filtros=[{'col': 'fecha', 'desde': '2026-01-01', 'hasta': '2026-01-31'}])
        self.assertEqual(self.ofs(res), ['50000'])
        res = self.consulta(filtros=[{'col': 'fecha_reg', 'desde': '2026-01-02', 'hasta': None}])
        self.assertEqual(self.ofs(res), ['50002', '50001'])

    def test_filtro_de_ubicacion_usa_la_del_grabado(self):
        grabado = self.registrar(of='22741', tipo='DIRECTO')['grabado']
        servicio.cambiar_ubicacion(grabado_id=grabado.id, ubicacion='GBB-2-1', usuario=self.operario)
        enlazada = self.orden('22741', date(2026, 1, 1), ubicacion='Cajón viejo')
        LegadoOrden.objects.create(orden=enlazada, rol='USO', grabado=grabado)
        self.orden('60000', date(2026, 1, 2), ubicacion='Cajón viejo')
        self.assertEqual(self.ofs(self.consulta(filtros=[{'col': 'ubicacion', 'in': ['GBB-2-1']}])), ['22741'])
        self.assertEqual(self.ofs(self.consulta(filtros=[{'col': 'ubicacion', 'in': ['Cajón viejo']}])), ['60000'])

    def test_filtros_invalidos(self):
        for filtros in ({'cliente': ['X']}, [{'col': 'nada', 'in': ['X']}], [{'col': 'temp', 'in': ['abc']}],
                        [{'col': 'fecha', 'desde': '10/01/2026'}], [{'col': ['of'], 'in': []}],
                        [{'col': 'of', 'in': ['1'] * (selectors.MAX_VALORES_FILTRO + 1)}]):
            with self.subTest(filtros=filtros):
                self.assertEqual(self.consulta(filtros=filtros, esperado=400)['status'], 'error')
        respuesta = self.client.get(reverse('grabados:api_consulta_registros'), {'filtros': '{malo'})
        self.assertEqual(respuesta.status_code, 400)

    def test_valores_de_columna_de_todo_el_historial(self):
        self.orden('10567', date(2019, 2, 1), cliente='VIEJO SA')
        self.orden('20000', date(2026, 10, 1), cliente='NUEVO SA')
        url = reverse('grabados:api_consulta_valores')
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_CONSULTA', 1):
            valores = self.client.get(url, {'col': 'cliente'}).json()['valores']
        self.assertEqual(sorted(valores), ['NUEVO SA', 'VIEJO SA'])
        self.assertEqual(self.client.get(url, {'col': 'cliente', 'q': '10567'}).json()['valores'], ['VIEJO SA'])
        self.assertEqual(self.client.get(url, {'col': 'nada'}).status_code, 400)

    def test_kpis_no_dependen_del_tope_ni_de_la_fecha(self):
        viejo = date(2018, 1, 1)
        for i, estado in enumerate(['EN_MAQUINA', 'EN_MAQUINA', 'REPETIR', 'PENDIENTE', 'PENDIENTE', 'PENDIENTE']):
            self.orden(str(70000 + i), viejo, estado=estado, fecha_programada=None)
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_CONSULTA', 1):
            kpis = self.client.get(reverse('grabados:api_kpis')).json()
        self.assertEqual((kpis['en_maquina'], kpis['para_repetir'], kpis['pendientes']), (2, 1, 3))
        # Terminados hoy: por actualizado_el (hoy), no por la fecha de registro.
        self.assertEqual(kpis['completados_hoy'], 1)

    def test_estadisticas_siguen_recibiendo_todo(self):
        for i in range(3):
            self.orden(str(80000 + i), date(2026, 1, 1))
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_CONSULTA', 1):
            datos = self.client.get(reverse('grabados:api_registros')).json()
        self.assertEqual(len(datos), 3)

    def test_pantalla_sin_filtro_de_fecha_por_defecto(self):
        js = (Path(__file__).parent / 'static' / 'js' / 'grabados_tabla.js').read_text(encoding='utf-8')
        self.assertNotIn('setMonth(', js)
        self.assertIn('/grabados/api/consulta/registros/', js)


class ConsultaGrabadosNuevosTests(BaseGrabados):
    """Tabla de Consultas con lo nuevo: grabados sin LegadoOrden como filas "Nuevo"
    junto a OrdenFabricacion ("Sistema anterior"), con búsqueda, filtros, tope y
    tarjetas sobre los dos orígenes."""

    def setUp(self):
        self.client.force_login(self.operario)

    def orden(self, of, registro, proceso='STAMPING', **campos):
        datos = {'descripcion': 'ANILLA', 'cliente': 'CLIENTE SA', 'estado': 'COMPLETADO',
                 'fecha_registro': registro}
        datos.update(campos)
        return OrdenFabricacion.objects.create(of=of, proceso=proceso, **datos)

    def grabado(self, of, creado=None, tipo='DIRECTO', **cambios):
        grabado = self.registrar(of=of, tipo=tipo)['grabado']
        if creado:
            cambios['creado_el'] = datetime(creado.year, creado.month, creado.day, 12, tzinfo=dt_timezone.utc)
        if cambios:
            Grabado.objects.filter(pk=grabado.pk).update(**cambios)
        return Grabado.objects.get(pk=grabado.pk)

    def migrado(self, of, registro, **campos):
        """Fila vieja ya migrada: su grabado tiene LegadoOrden y no sale como "Nuevo"."""
        grabado = self.grabado(of)
        orden = self.orden(of, registro, **campos)
        LegadoOrden.objects.create(orden=orden, rol='USO', grabado=grabado)
        return orden, grabado

    def consulta(self, q='', filtros=None):
        params = {'q': q}
        if filtros is not None:
            params['filtros'] = json.dumps(filtros)
        respuesta = self.client.get(reverse('grabados:api_consulta_registros'), params)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return respuesta.json()

    def ids(self, respuesta):
        return [r['id'] for r in respuesta['data']]

    # ------------------------------------------------------------ filas
    def test_grabado_nuevo_es_una_fila_y_el_migrado_no_se_duplica(self):
        nuevo = self.grabado('30000', creado=date(2026, 10, 5), ubicacion='GBB-1-2')
        orden, migrado = self.migrado('30001', date(2026, 10, 1))
        res = self.consulta()
        self.assertEqual(self.ids(res), [f'g{nuevo.id}', orden.id])
        self.assertEqual(res['total'], 2)
        self.assertNotIn(f'g{migrado.id}', self.ids(res))

        fila = res['data'][0]
        self.assertEqual(
            {k: fila[k] for k in ('origen', 'of', 'cliente', 'desc', 'proceso', 'estado', 'tipo',
                                  'ubicacion', 'sobre', 'fecha', 'fecha_reg', 'ref', 'maquina')},
            {'origen': 'Nuevo', 'of': '30000', 'cliente': 'CLIENTE SA', 'desc': 'ANILLA DORADA',
             'proceso': 'STAMPING', 'estado': 'APROBADO', 'tipo': 'DIRECTO', 'ubicacion': 'GBB-1-2',
             'sobre': 'S-10', 'fecha': '—', 'fecha_reg': '05/10/2026', 'ref': None, 'maquina': None})
        self.assertEqual((fila['grabado']['id'], fila['grabado']['ubicacion'], fila['motivo_sin_grabado']),
                         (nuevo.id, 'GBB-1-2', None))
        self.assertEqual(fila['grabado_mod'], selectors.formatear_fecha(nuevo.actualizado_el))
        self.assertEqual(res['data'][1]['origen'], 'Sistema anterior')

    def test_mezcla_por_fecha_de_registro_con_tope(self):
        reciente = self.orden('100', date(2026, 10, 1))
        vieja = self.orden('101', date(2026, 8, 1))
        sin_fecha = self.orden('102', None)
        medio = self.grabado('200', creado=date(2026, 9, 15))
        mismo_dia = self.grabado('201', creado=date(2026, 10, 1))   # el mismo día: primero lo nuevo
        res = self.consulta()
        self.assertEqual(self.ids(res), [f'g{mismo_dia.id}', reciente.id, f'g{medio.id}', vieja.id, sin_fecha.id])
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_CONSULTA', 3):
            res = self.consulta()
        self.assertEqual(self.ids(res), [f'g{mismo_dia.id}', reciente.id, f'g{medio.id}'])
        self.assertEqual((res['total'], res['limite']), (5, 3))

    def test_tope_entre_los_dos_origenes_mira_todo_el_historial(self):
        for i in range(3):
            self.grabado(str(300 + i), creado=date(2026, 10, 1 + i))
        viejo = self.grabado('399', creado=date(2020, 1, 1))
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_CONSULTA', 2):
            self.assertNotIn(f'g{viejo.id}', self.ids(self.consulta()))
            res = self.consulta(q='399')
        self.assertEqual((self.ids(res), res['total']), ([f'g{viejo.id}'], 1))

    # ------------------------------------------------------- búsqueda
    def test_busqueda_en_filas_nuevas_y_por_of_del_envio(self):
        grabado = self.grabado('22741', cliente='TABACALERA DEL CIBAO', ubicacion='GBB-4-4')
        self.orden('40000', date(2026, 1, 1))
        for q in ('22741', 'cibao', 'gbb-4', 'aprobado', 'tabacalera stamping'):
            with self.subTest(q=q):
                self.assertEqual(self.ids(self.consulta(q=q)), [f'g{grabado.id}'])
        servicio.mandar_a_maquina(of='30500', proceso='STAMPING', grabado_id=grabado.id,
                                  fila=fila_excel(), usuario=self.operario)
        # La OF del PLANI no es fila propia: buscarla encuentra el grabado con que se tiró.
        res = self.consulta(q='30500')
        self.assertEqual((self.ids(res), res['total']), ([f'g{grabado.id}'], 1))
        self.assertEqual(self.consulta(q='nada')['total'], 0)

    # -------------------------------------------------------- filtros
    def test_filtro_por_origen(self):
        nuevo = self.grabado('30000')
        orden = self.orden('40000', date(2026, 1, 1))
        self.assertEqual(self.ids(self.consulta(filtros=[{'col': 'origen', 'in': ['Nuevo']}])), [f'g{nuevo.id}'])
        self.assertEqual(self.ids(self.consulta(filtros=[{'col': 'origen', 'fuera': ['Nuevo']}])), [orden.id])
        self.assertEqual(self.ids(self.consulta(filtros=[{'col': 'origen', 'in': ['Sistema anterior']}])),
                         [orden.id])
        self.assertEqual(self.consulta(filtros=[{'col': 'origen', 'in': []}])['total'], 0)

    def test_filtros_por_columna_en_filas_nuevas(self):
        nuevo = self.grabado('30000', cliente='NUEVO SA')
        orden = self.orden('40000', date(2026, 1, 1), cliente='VIEJO SA', maquina='GIETZ 01')
        casos = [
            ([{'col': 'cliente', 'in': ['NUEVO SA']}], [f'g{nuevo.id}']),
            ([{'col': 'cliente', 'fuera': ['NUEVO SA']}], [orden.id]),
            ([{'col': 'estado', 'in': ['APROBADO']}], [f'g{nuevo.id}']),
            ([{'col': 'tipo', 'in': ['DIRECTO']}], [f'g{nuevo.id}']),
            # Columnas que el grabado no tiene: vacías ('—').
            ([{'col': 'maquina', 'in': ['—']}], [f'g{nuevo.id}']),
            ([{'col': 'maquina', 'in': ['GIETZ 01']}], [orden.id]),
            ([{'col': 'maquina', 'fuera': ['—']}], [orden.id]),
            ([{'col': 'temp', 'in': ['150']}], []),
        ]
        for filtros, esperado in casos:
            with self.subTest(filtros=filtros):
                self.assertEqual(self.ids(self.consulta(filtros=filtros)), esperado)

    def test_boton_fecha_y_fecha_de_registro(self):
        nuevo = self.grabado('30000', creado=date(2026, 9, 10))
        orden = self.orden('40000', date(2026, 9, 1), fecha_programada=date(2026, 9, 5))
        # Fecha Prog.: el grabado no tiene, así que no pasa (como una fila vieja sin fecha).
        res = self.consulta(filtros=[{'col': 'fecha', 'desde': '2026-09-01', 'hasta': '2026-09-30'}])
        self.assertEqual(self.ids(res), [orden.id])
        res = self.consulta(filtros=[{'col': 'fecha_reg', 'desde': '2026-09-05', 'hasta': '2026-09-30'}])
        self.assertEqual(self.ids(res), [f'g{nuevo.id}'])
        res = self.consulta(filtros=[{'col': 'fecha_reg', 'desde': None, 'hasta': '2026-09-09'}])
        self.assertEqual(self.ids(res), [orden.id])

    def test_valores_de_columna_juntan_los_dos_origenes(self):
        self.grabado('30000', cliente='NUEVO SA')
        self.orden('40000', date(2026, 1, 1), cliente='VIEJO SA', maquina='GIETZ 01')
        url = reverse('grabados:api_consulta_valores')
        valores = lambda **p: self.client.get(url, p).json()['valores']
        self.assertEqual(sorted(valores(col='cliente')), ['NUEVO SA', 'VIEJO SA'])
        self.assertEqual(sorted(valores(col='origen')), ['Nuevo', 'Sistema anterior'])
        self.assertIn(None, valores(col='maquina'))            # la fila nueva no tiene máquina
        self.assertEqual(valores(col='origen', q='30000'), ['Nuevo'])
        self.assertIn('APROBADO', valores(col='estado'))

    # -------------------------------------------- panel y ubicación
    def test_ubicacion_de_fila_nueva_se_edita_con_cambiar_ubicacion(self):
        nuevo = self.grabado('30000', ubicacion='GBB-1-1')
        url = reverse('grabados:api_grabado_ubicacion', kwargs={'grabado_id': nuevo.id})
        respuesta = self.client.post(url, data=json.dumps({'ubicacion': 'gbb-2-3', 'origen': 'CONSULTA'}),
                                     content_type='application/json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        cambio = HistorialUbicacion.objects.get(grabado=nuevo)
        self.assertEqual((cambio.ubicacion_anterior, cambio.ubicacion_nueva, cambio.origen),
                         ('GBB-1-1', 'GBB-2-3', 'CONSULTA'))
        fila = self.consulta()['data'][0]
        self.assertEqual((fila['ubicacion'], fila['grabado']['ubicacion']), ('GBB-2-3', 'GBB-2-3'))

    def test_pantalla_con_columna_origen_y_panel_para_filas_nuevas(self):
        html = self.client.get(reverse('grabados:grabado_consulta')).content.decode()
        self.assertIn('data-col="origen"', html)
        self.assertIn('data-columna="origen"', html)
        js = (Path(__file__).parent / 'static' / 'js' / 'grabados_tabla.js').read_text(encoding='utf-8')
        self.assertIn("'origen'", js)
        self.assertIn('window.PanelGrabado.abrir(reg.grabado.id);', js)

    # ------------------------------------------------------- tarjetas
    def test_tarjetas_suman_lo_nuevo_sin_contar_dos_veces(self):
        viejo = date(2018, 1, 1)
        # En máquina: fila vieja sin migrar + envío abierto. La fila vieja migrada con grabado
        # no cuenta (la representa su grabado/envío).
        self.orden('70000', viejo, estado='EN_MAQUINA')
        self.migrado('70001', viejo, estado='EN_MAQUINA')
        en_maquina = self.grabado('70002')
        servicio.mandar_a_maquina(of='71000', proceso='STAMPING', grabado_id=en_maquina.id,
                                  fila=fila_excel(), usuario=self.operario)
        # Terminados hoy: envío recogido hoy (OK).
        recogido = self.grabado('70003')
        envio = servicio.mandar_a_maquina(of='71001', proceso='STAMPING', grabado_id=recogido.id,
                                          fila=fila_excel(), usuario=self.operario)
        servicio.recoger_de_maquina(envio_id=envio.id, of='71001', estado_fisico='OK', ubicacion='GBB-1-1',
                                    comentario='', usuario=self.operario)
        # Para repetir: fila vieja REPETIR sin migrar + todos los grabados en REPETIR; la fila
        # vieja migrada cuenta una sola vez, por su grabado.
        self.orden('70004', viejo, estado='REPETIR')
        _, repetir_migrado = self.migrado('70005', viejo, estado='REPETIR')
        Grabado.objects.filter(pk=repetir_migrado.pk).update(estado='REPETIR')
        self.grabado('70006', estado='REPETIR')
        # Pendientes: fila vieja PENDIENTE (SIN_MIGRAR) + grabados en fabricación o K1.
        pendiente = self.orden('70007', viejo, estado='PENDIENTE')
        LegadoOrden.objects.create(orden=pendiente, rol='SIN_MIGRAR', grabado=None)
        self.registrar(of='70008', tipo='K1')                        # PENDIENTE_K1
        self.grabado('70009', estado='EN_FABRICACION')

        kpis = self.client.get(reverse('grabados:api_kpis')).json()
        self.assertEqual((kpis['en_maquina'], kpis['para_repetir'], kpis['pendientes']), (2, 3, 3))
        # Hoy: el envío recogido + las 2 filas viejas REPETIR (actualizado_el = hoy al crearlas).
        self.assertEqual(kpis['completados_hoy'], 3)


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

    def recoger(self, envio, of='22741', estado='OK', ubicacion='GBB-1-1', comentario=''):
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
        self.assertEqual((propia['grabado']['tipo'], propia['grabado']['tipo_display']), ('K1', 'K1 – de prueba'))
        self.assertFalse(propia['grabado']['usa_grabado_de_otra'])
        self.assertEqual((ajena['accion'], ajena['grabado']['id']), (selectors.PLANI_MANDAR, grabado.id))
        self.assertTrue(ajena['grabado']['usa_grabado_de_otra'])

    def test_grabado_directo_se_puede_mandar_enseguida(self):
        grabado = self.registrar(tipo='DIRECTO', maquina_id='')['grabado']
        (estado,) = self.resolver(fila_plani('22741'))
        self.assertEqual((estado['accion'], estado['grabado']['id']), (selectors.PLANI_MANDAR, grabado.id))
        self.assertEqual(estado['grabado']['tipo'], 'DIRECTO')
        envio = self.mandar(grabado)
        self.assertEqual(envio.grabado.estado, 'EN_MAQUINA')

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
        self.recoger(envio, ubicacion='GBB-2-1')
        (estado,) = self.resolver(fila_plani('22741'))
        self.assertEqual(estado['accion'], selectors.PLANI_MANDAR_OTRA_VEZ)
        self.assertEqual(estado['completada']['ubicacion'], 'GBB-2-1')
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

    def test_plani_aplica_los_alias_de_maquina(self):
        self.assertEqual(servicio.normalizar_nombre_maquina(' stan  foil '), 'STAR FOIL')
        self.assertEqual(servicio.normalizar_nombre_maquina('StarFoil'), 'STAR FOIL')
        star_foil = Maquina.objects.create(nombre='STAR FOIL')
        envio = self.mandar(self.aprobado(), maquina='stan foil')
        self.assertEqual(envio.maquina, star_foil)

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
        self.recoger(self.envio, ubicacion='GBB-1-1', comentario='Todo bien')
        self.envio.refresh_from_db()
        self.grabado.refresh_from_db()
        self.assertEqual((self.grabado.estado, self.grabado.usos_acumulados, self.grabado.ubicacion),
                         ('APROBADO', 1, 'GBB-1-1'))
        self.assertEqual((self.envio.estado_fisico, self.envio.ubicacion, self.envio.recogido_por),
                         ('OK', 'GBB-1-1', self.operario))
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
            'envio_id': envio.id, 'of': '22741', 'estado_fisico': 'OK', 'ubicacion': 'GBB-3-1', 'comentario': '',
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
                                                                                   'estado_fisico': 'OK', 'ubicacion': 'GBB-1-1'})
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


# ============================================================
# PERMISOS POR ÁREA (módulo Grabados, ver apps/core/permisos.py)
# ============================================================

class PermisosModuloGrabadosTests(BaseGrabados):

    PAGINAS = ('alta_grabado', 'k1_pendientes', 'inventario_grabados', 'plani_consulta',
               'grabado_consulta', 'grabado_estadisticas')

    def rutas(self):
        """(nombre, url) de TODAS las rutas del módulo, con valores de relleno
        para los parámetros: así una ruta nueva queda cubierta sin tocar el test."""
        from apps.gestion_grabados.urls import urlpatterns
        for patron in urlpatterns:
            kwargs = {nombre: 1 for nombre in patron.pattern.converters}
            yield patron.name, reverse(f'grabados:{patron.name}', kwargs=kwargs)

    def test_sin_acceso_todas_las_rutas_dan_403(self):
        self.client.force_login(self.sin_acceso)
        prueba = self.registrar()['prueba_k1']
        for nombre, url in self.rutas():
            for metodo in ('get', 'post'):
                with self.subTest(ruta=nombre, metodo=metodo):
                    respuesta = getattr(self.client, metodo)(url)
                    self.assertEqual(respuesta.status_code, 403)
                    if '/api/' in url:
                        self.assertEqual(respuesta.json()['status'], 'error')
                    else:
                        self.assertTemplateUsed(respuesta, '403.html')
        prueba.refresh_from_db()
        self.assertEqual(prueba.resultado, 'PENDIENTE')   # nada se decidió

    def test_sin_sesion_todas_las_rutas_mandan_al_login(self):
        for nombre, url in self.rutas():
            with self.subTest(ruta=nombre):
                respuesta = self.client.get(url)
                self.assertEqual(respuesta.status_code, 302)
                self.assertIn('login', respuesta['Location'])

    def test_con_acceso_entra_a_las_pantallas(self):
        self.client.force_login(self.operario)
        for nombre in self.PAGINAS:
            with self.subTest(pantalla=nombre):
                self.assertEqual(self.client.get(reverse(f'grabados:{nombre}')).status_code, 200)
        self.assertEqual(self.client.get(reverse('grabados:api_inventario')).status_code, 200)
        self.assertEqual(self.client.get(reverse('grabados:api_k1_pendientes')).status_code, 200)

    def test_superusuario_entra_sin_estar_en_el_grupo(self):
        self.assertFalse(self.admin.groups.exists())
        self.client.force_login(self.admin)
        for nombre in self.PAGINAS:
            with self.subTest(pantalla=nombre):
                self.assertEqual(self.client.get(reverse(f'grabados:{nombre}')).status_code, 200)
        prueba = self.registrar()['prueba_k1']
        respuesta = self.client.post(reverse('grabados:api_k1_aprobar', args=[prueba.id]),
                                     data='{}', content_type='application/json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)

    def test_menu_con_solo_grabados(self):
        self.client.force_login(self.operario)
        respuesta = self.client.get(reverse('core:dashboard'))
        self.assertContains(respuesta, 'Gestión de Grabados')
        self.assertContains(respuesta, reverse('grabados:k1_pendientes'))
        for oculto in ('Gestión de Tintas', 'Gestión de TI', '<span>Configuración</span>',
                       '<span>Usuarios</span>', '<span>Reportes</span>', 'Tintas disponibles'):
            with self.subTest(oculto=oculto):
                self.assertNotContains(respuesta, oculto)

    def test_menu_sin_ningun_modulo(self):
        self.client.force_login(self.sin_acceso)
        respuesta = self.client.get(reverse('core:dashboard'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, 'Gestión de Grabados')
        self.assertNotContains(respuesta, '/grabados/')

    def test_menu_del_superusuario_tiene_todo(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('core:dashboard'))
        for visible in ('Gestión de Grabados', 'Gestión de Tintas', 'Gestión de TI',
                        '<span>Configuración</span>', '<span>Usuarios</span>', '<span>Reportes</span>'):
            with self.subTest(visible=visible):
                self.assertContains(respuesta, visible)

    def test_eliminar_sigue_exigiendo_is_staff(self):
        OrdenFabricacion.objects.create(of='22741', proceso='STAMPING')
        url = reverse('grabados:api_eliminar')
        datos = json.dumps({'of': '22741', 'proceso': 'STAMPING'})

        self.client.force_login(self.operario)   # con acceso al módulo, sin is_staff
        respuesta = self.client.post(url, data=datos, content_type='application/json')
        self.assertEqual(respuesta.status_code, 403)
        self.assertIn('eliminar', respuesta.json()['message'])
        self.assertTrue(OrdenFabricacion.objects.exists())

        staff_sin_modulo = User.objects.create_user('staff_sin_modulo', password='x', is_staff=True)
        self.client.force_login(staff_sin_modulo)
        self.assertEqual(self.client.post(url, data=datos, content_type='application/json').status_code, 403)
        self.assertTrue(OrdenFabricacion.objects.exists())

        self.supervisor.is_staff = True
        self.supervisor.save()
        self.client.force_login(self.supervisor)
        respuesta = self.client.post(url, data=datos, content_type='application/json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertFalse(OrdenFabricacion.objects.exists())

    def test_grupo_grabados_y_supervisores_eliminado(self):
        grupo = Group.objects.get(name=GRUPO_GRABADOS)
        self.assertTrue(grupo.permissions.filter(codename='acceso_grabados',
                                                 content_type__app_label='core').exists())
        self.assertFalse(Group.objects.filter(name='Supervisores de Producción').exists())
        self.assertFalse(Permission.objects.filter(codename='decidir_pruebak1').exists())

    def test_migracion_mete_en_el_grupo_a_activos_y_supervisores(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps as django_apps
        from django.db import connection

        migracion = import_module('apps.core.migrations.0002_grupo_grabados')
        activo = User.objects.create_user('activo', password='x')
        inactivo = User.objects.create_user('inactivo', password='x', is_active=False)
        supervisor_inactivo = User.objects.create_user('sup_inactivo', password='x', is_active=False)
        supervisor_inactivo.groups.add(Group.objects.create(name='Supervisores de Producción'))

        config_core = django_apps.get_app_config('core')
        models_module = config_core.models_module
        try:
            migracion.crear_grupo_grabados(django_apps, SimpleNamespace(connection=connection))
        finally:
            config_core.models_module = models_module

        miembros = set(Group.objects.get(name=GRUPO_GRABADOS).user_set.values_list('username', flat=True))
        self.assertIn('activo', miembros)
        self.assertIn('sup_inactivo', miembros)
        self.assertNotIn('inactivo', miembros)
        self.assertTrue(activo.has_perm('core.acceso_grabados'))
        self.assertFalse(User.objects.get(pk=inactivo.pk).groups.exists())


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
                self.assertIn('Crear Grabado', respuesta.json()['message'])
        self.assertFalse(OrdenFabricacion.objects.exists())

    def test_pantallas_legadas_marcadas_como_historico(self):
        self.assertContains(self.client.get(reverse('grabados:grabado_estadisticas')), '(Histórico)')
        # La Tabla de Consultas ya junta el histórico con los grabados nuevos.
        respuesta = self.client.get(reverse('grabados:grabado_consulta'))
        self.assertNotContains(respuesta, '(Histórico)')
        self.assertContains(respuesta, 'Registro de Órdenes y Grabados')

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
        self.assertEqual(grabado.tipo, 'LEGADO')
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

    def test_unifica_alias_de_star_foil_y_no_crea_maquinas_que_son_ubicaciones(self):
        for of, nombre in (('22741', 'STAR FOIL'), ('22742', 'STARFOIL'), ('22743', 'stan foil'),
                           ('22744', 'GBB-1-3')):
            self.orden(of, maquina=nombre)
        salida = self.migrar('--aplicar', '--limite', '0')

        self.assertEqual(sorted(Maquina.objects.values_list('nombre', flat=True)),
                         ['SIN MÁQUINA (LEGADO)', 'STAR FOIL'])
        self.assertEqual(EnvioMaquina.objects.filter(maquina__nombre='STAR FOIL').count(), 3)
        gbb = OrdenFabricacion.objects.get(of='22744')
        self.assertEqual(gbb.legado.envio.maquina.nombre, 'SIN MÁQUINA (LEGADO)')
        # La fila ya tenía ubicación: se deja esa.
        self.assertEqual(gbb.legado.grabado.ubicacion, 'CAJON A1')
        self.assertIn(f"id={gbb.id} OF=22744 STAMPING COMPLETADO máquina='GBB-1-3' -> "
                      "la fila ya tenía ubicación 'CAJON A1', se deja esa", salida)

    def test_maquina_que_es_ubicacion_llena_la_ubicacion_vacia(self):
        orden = self.orden('17471', estado='COMPLETADO', maquina=' gbb-1-3 ', ubicacion='')
        salida = self.migrar('--aplicar', '--limite', '0')

        grabado = Grabado.objects.get()
        self.assertEqual(grabado.ubicacion, 'GBB-1-3')
        self.assertEqual((orden.legado.envio.ubicacion, orden.legado.envio.maquina.nombre),
                         ('GBB-1-3', 'SIN MÁQUINA (LEGADO)'))
        self.assertFalse(Maquina.objects.filter(nombre='GBB-1-3').exists())
        self.assertIn("se usa como ubicación de la fila: 'GBB-1-3'", salida)
        self.assertRegex(salida, r'tomada de la columna máquina\s+1')
        orden.refresh_from_db()     # OrdenFabricacion no se toca
        self.assertEqual((orden.maquina, orden.ubicacion), (' gbb-1-3 ', ''))

    def test_ubicacion_de_la_fila_mas_reciente_sin_validar_el_formato(self):
        self.orden('22741', ubicacion='Cajón viejo')
        segunda = self.orden('22900', referencia='22741', ubicacion='GBB-2-1',
                             creado=DESPUES_DEL_CORTE + timedelta(days=2))
        self.orden('22950', referencia='22741', ubicacion='',   # la más reciente no tiene: se salta
                   creado=DESPUES_DEL_CORTE + timedelta(days=5))
        self.orden('23000', ubicacion='Estante 3')                # formato viejo, sin validar
        salida = self.migrar('--aplicar', '--limite', '0')

        self.assertEqual(Grabado.objects.get(of_origen='22741').ubicacion, 'GBB-2-1')
        self.assertEqual(Grabado.objects.get(of_origen='23000').ubicacion, 'ESTANTE 3')
        self.assertFalse(HistorialUbicacion.objects.exists())
        self.assertIn(f"22741 STAMPING: queda 'GBB-2-1' (fila id={segunda.id} OF=22900", salida)
        self.assertIn("otras: 'CAJÓN VIEJO'", salida)
        self.assertRegex(salida, r'con el formato nuevo \(ej. GBB-2-1\)\s+1')
        self.assertRegex(salida, r'con formato viejo \(se deja hasta que la editen\)\s+1')

    def test_normaliza_ubicacion_a_mayusculas_sin_espacios_sobrantes(self):
        orden = self.orden('22741', ubicacion='  Gbb-2-1 ')
        self.orden('22742', ubicacion='Cajón   b2')     # formato viejo: se normaliza, no se valida
        self.orden('22743', ubicacion='GBB-3-3')        # ya normalizada: no cuenta
        salida = self.migrar('--aplicar', '--limite', '0')

        self.assertEqual(Grabado.objects.get(of_origen='22741').ubicacion, 'GBB-2-1')
        self.assertEqual(Grabado.objects.get(of_origen='22742').ubicacion, 'CAJÓN B2')
        self.assertEqual(orden.legado.envio.ubicacion, 'GBB-2-1')
        self.assertRegex(salida, r'Filas con la ubicación normalizada \(mayúsculas / espacios\)\s+2')
        self.assertIn(f"ejemplo: '  Gbb-2-1 ' -> 'GBB-2-1' (fila id={orden.id} OF=22741)", salida)
        self.assertRegex(salida, r'con el formato nuevo \(ej. GBB-2-1\)\s+2')
        orden.refresh_from_db()     # OrdenFabricacion no se toca
        self.assertEqual(orden.ubicacion, '  Gbb-2-1 ')

    def test_grabado_reutilizado_conserva_su_ubicacion(self):
        existente = Grabado.objects.create(of_origen='22741', proceso='STAMPING', cliente='ALTA',
                                           referencia='ALTA', estado='APROBADO', ubicacion='GBB-9-9',
                                           tipo='K1')
        self.orden('22741', ubicacion='CAJON A1')
        self.migrar('--aplicar')
        existente.refresh_from_db()
        self.assertEqual(existente.ubicacion, 'GBB-9-9')

    def test_pantalla_resume_listas_y_el_archivo_va_completo(self):
        for i in range(12):
            self.orden(str(24000 + i))
        salida = self.migrar()
        self.assertRegex(salida, r'No encontradas .*\s+12')
        self.assertIn('OF 24009 STAMPING', salida)
        self.assertNotIn('OF 24010 STAMPING', salida)
        self.assertIn('... y 2 más (completo en el archivo del reporte)', salida)
        archivo = os.path.join(self.salida, os.listdir(self.salida)[0])
        with open(archivo, encoding='utf-8') as reporte:
            self.assertIn('OF 24011 STAMPING', reporte.read())

    def test_reporte_separa_grabados_nuevos_de_los_que_ya_estaban_y_cuenta_envios(self):
        Grabado.objects.create(of_origen='30000', proceso='STAMPING', cliente='ALTA',
                               referencia='ALTA', estado='PENDIENTE_K1', tipo='K1')
        self.orden('22741')
        self.orden('22742', estado='EN_MAQUINA')
        self.orden('22743', estado='EN_PROCESO')
        salida = self.migrar()

        nuevos, previos = salida.split('Grabados que crea la migración, por estado:')[1].split(
            'Grabados que ya estaban en la base')
        self.assertNotIn('PENDIENTE_K1', nuevos)
        self.assertIn('EN_MAQUINA', nuevos)
        self.assertIn('PENDIENTE_K1', previos.split('Fabricaciones')[0])
        self.assertRegex(salida, r'cerrados \(COMPLETADO / REPETIR / REVISION\)\s+1')
        self.assertRegex(salida, r'abiertos \(EN_MAQUINA\)\s+1')

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
                                           referencia='ALTA', estado='PENDIENTE_K1', usos_acumulados=2,
                                           tipo='K1')
        self.orden('22741', usos_acumulados=5)
        salida = self.migrar('--aplicar')

        self.assertEqual(Grabado.objects.count(), 1)
        existente.refresh_from_db()
        self.assertEqual((existente.estado, existente.tipo, existente.cliente),
                         ('PENDIENTE_K1', 'K1', 'ALTA'))
        self.assertEqual(existente.usos_acumulados, 7)
        self.assertIn('el grabado creado en EIS está en PENDIENTE_K1', salida)

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


# ============================================================
# GRABADO INACTIVO: desactivar / reactivar y protección del historial
# ============================================================

class DesactivarGrabadoTests(BasePlani):

    def desactivar(self, grabado, motivo='Grabado dañado', usuario=None):
        return servicio.desactivar_grabado(grabado_id=grabado.id, motivo=motivo,
                                           usuario=usuario or self.operario)

    def reactivar(self, grabado, usuario=None):
        return servicio.reactivar_grabado(grabado_id=grabado.id, usuario=usuario or self.supervisor)

    # ---------------------------------------------------------- servicio
    def test_desactivar_guarda_quien_cuando_motivo_y_estado_previo(self):
        grabado = self.aprobado()
        self.desactivar(grabado, motivo='  Grabado dañado  ')
        grabado.refresh_from_db()
        self.assertEqual(grabado.estado, 'INACTIVO')
        self.assertEqual(grabado.get_estado_display(), 'Inactivo')
        self.assertEqual(grabado.estado_previo, 'APROBADO')
        self.assertEqual(grabado.desactivado_por, self.operario)
        self.assertIsNotNone(grabado.desactivado_el)
        self.assertEqual(grabado.motivo_desactivacion, 'Grabado dañado')
        cambio = HistorialActivacion.objects.get(grabado=grabado)
        self.assertEqual((cambio.accion, cambio.estado, cambio.motivo, cambio.usuario),
                         ('DESACTIVAR', 'APROBADO', 'Grabado dañado', self.operario))
        self.assertIsNotNone(cambio.fecha)

    def test_motivo_obligatorio(self):
        grabado = self.aprobado()
        for motivo in ('', '   ', 'abc', None):
            with self.subTest(motivo=motivo):
                with self.assertRaisesMessage(servicio.ErrorGrabado, 'motivo de la desactivación es obligatorio'):
                    self.desactivar(grabado, motivo=motivo)
        grabado.refresh_from_db()
        self.assertEqual(grabado.estado, 'APROBADO')
        self.assertFalse(HistorialActivacion.objects.exists())

    def test_en_maquina_no_se_puede_desactivar(self):
        grabado = self.aprobado()
        self.mandar(grabado)
        with self.assertRaisesMessage(servicio.TransicionInvalida, 'está en máquina: recógelo'):
            self.desactivar(grabado)
        grabado.refresh_from_db()
        self.assertEqual(grabado.estado, 'EN_MAQUINA')
        self.assertFalse(HistorialActivacion.objects.exists())

    def test_ya_inactivo_no_se_vuelve_a_desactivar(self):
        grabado = self.aprobado()
        self.desactivar(grabado)
        with self.assertRaisesMessage(servicio.TransicionInvalida, 'ya está inactivo'):
            self.desactivar(grabado)
        self.assertEqual(HistorialActivacion.objects.count(), 1)

    def test_grabado_inexistente(self):
        with self.assertRaises(servicio.GrabadoNoExiste):
            servicio.desactivar_grabado(grabado_id=999999, motivo='Grabado dañado', usuario=self.operario)
        with self.assertRaises(servicio.GrabadoNoExiste):
            servicio.reactivar_grabado(grabado_id=999999, usuario=self.operario)

    def test_reactivar_vuelve_al_estado_previo_y_deja_rastro(self):
        for estado in ('APROBADO', 'REPETIR', 'EN_FABRICACION'):
            with self.subTest(estado=estado):
                grabado = self.aprobado(of=f'4{len(estado)}000')
                Grabado.objects.filter(pk=grabado.pk).update(estado=estado)
                self.desactivar(grabado)
                self.reactivar(grabado)
                grabado.refresh_from_db()
                self.assertEqual(grabado.estado, estado)
                self.assertEqual((grabado.estado_previo, grabado.motivo_desactivacion), ('', ''))
                self.assertIsNone(grabado.desactivado_por)
                self.assertIsNone(grabado.desactivado_el)
                ultimo = HistorialActivacion.objects.filter(grabado=grabado).first()
                self.assertEqual((ultimo.accion, ultimo.estado, ultimo.usuario),
                                 ('REACTIVAR', estado, self.supervisor))
                self.assertEqual(HistorialActivacion.objects.filter(grabado=grabado).count(), 2)

    def test_reactivar_un_grabado_activo_da_error(self):
        grabado = self.aprobado()
        with self.assertRaisesMessage(servicio.TransicionInvalida, 'no está inactivo'):
            self.reactivar(grabado)
        self.assertFalse(HistorialActivacion.objects.exists())

    # ---------------------------------------------------------- bloqueos
    def test_inactivo_no_se_manda_a_maquina(self):
        grabado = self.aprobado()
        self.desactivar(grabado)
        with self.assertRaisesMessage(servicio.TransicionInvalida, 'está inactivo: no se puede mandar a máquina'):
            self.mandar(grabado)
        self.assertFalse(EnvioMaquina.objects.exists())
        (estado,) = self.resolver(fila_plani('22741'))
        self.assertEqual(estado['accion'], selectors.PLANI_INACTIVO)
        self.assertEqual(estado['grabado']['estado'], 'INACTIVO')

    def test_inactivo_no_cambia_de_ubicacion(self):
        grabado = self.aprobado()
        self.desactivar(grabado)
        with self.assertRaisesMessage(servicio.TransicionInvalida, 'no se puede cambiar su ubicación'):
            servicio.cambiar_ubicacion(grabado_id=grabado.id, ubicacion='GBB-1-1', usuario=self.operario)
        self.client.force_login(self.operario)
        respuesta = self.client.post(reverse('grabados:api_grabado_ubicacion', args=[grabado.id]),
                                     data=json.dumps({'ubicacion': 'GBB-1-1'}), content_type='application/json')
        self.assertEqual(respuesta.status_code, 409)
        self.assertFalse(HistorialUbicacion.objects.exists())

    def test_crear_grabado_bloquea_un_inactivo(self):
        grabado = self.aprobado()
        Grabado.objects.filter(pk=grabado.pk).update(estado='REPETIR')
        self.desactivar(grabado)
        evaluacion = servicio.evaluar_alta('22741', 'STAMPING', info_externa(), _normalizar_of)
        self.assertEqual(evaluacion['accion'], servicio.ACCION_BLOQUEADA)
        self.assertIn('inactivo', evaluacion['mensaje'])
        with self.assertRaisesMessage(servicio.TransicionInvalida, 'inactivo'):
            self.registrar()

    def test_k1_pendiente_queda_oculto_y_reaparece_al_reactivar(self):
        resultado = self.registrar()
        grabado, prueba = resultado['grabado'], resultado['prueba_k1']
        self.client.force_login(self.operario)
        url = reverse('grabados:api_k1_pendientes')

        self.desactivar(grabado)
        self.assertEqual(self.client.get(url).json()['data'], [])
        with self.assertRaisesMessage(servicio.TransicionInvalida, 'inactivo'):
            servicio.decidir_k1(prueba_id=prueba.id, usuario=self.supervisor, aprobar=True)
        prueba.refresh_from_db()
        self.assertEqual(prueba.resultado, 'PENDIENTE')

        self.reactivar(grabado)
        grabado.refresh_from_db()
        self.assertEqual(grabado.estado, 'PENDIENTE_K1')
        self.assertEqual([p['id'] for p in self.client.get(url).json()['data']], [prueba.id])
        servicio.decidir_k1(prueba_id=prueba.id, usuario=self.supervisor, aprobar=True)
        grabado.refresh_from_db()
        self.assertEqual(grabado.estado, 'APROBADO')

    # ---------------------------------------------------------- consulta
    def test_detalle_de_un_inactivo_trae_todo_su_historial(self):
        grabado = self.aprobado()
        envio = self.mandar(grabado)
        self.recoger(envio)
        self.desactivar(grabado)
        d = selectors.detalle_grabado(grabado.id)
        self.assertTrue(d['inactivo'])
        self.assertFalse(d['puede_desactivar'])
        self.assertEqual(d['estado'], 'INACTIVO')
        self.assertEqual(d['desactivacion']['por'], 'operario')
        self.assertEqual(d['desactivacion']['motivo'], 'Grabado dañado')
        self.assertEqual(d['desactivacion']['estado_previo'], 'APROBADO')
        self.assertIsNotNone(d['desactivacion']['el'])
        self.assertEqual(len(d['fabricaciones']), 1)
        self.assertEqual(len(d['pruebas_k1']), 1)
        self.assertEqual(len(d['envios']), 1)
        self.assertEqual(len(d['historial_ubicaciones']), 1)
        self.assertEqual([(c['accion'], c['usuario']) for c in d['historial_activaciones']],
                         [('DESACTIVAR', 'operario')])

        self.reactivar(grabado)
        d = selectors.detalle_grabado(grabado.id)
        self.assertFalse(d['inactivo'])
        self.assertTrue(d['puede_desactivar'])
        self.assertIsNone(d['desactivacion'])
        self.assertEqual([c['accion'] for c in d['historial_activaciones']], ['REACTIVAR', 'DESACTIVAR'])

    def test_detalle_en_maquina_no_puede_desactivar(self):
        grabado = self.aprobado()
        self.mandar(grabado)
        self.assertFalse(selectors.detalle_grabado(grabado.id)['puede_desactivar'])

    def test_inventario_tarjeta_y_filtro_inactivos(self):
        activo = self.aprobado()
        inactivo = self.aprobado(of='22800')
        self.desactivar(inactivo)
        self.client.force_login(self.operario)
        self.assertContains(self.client.get(reverse('grabados:inventario_grabados')), 'data-estado="INACTIVO"')
        res = self.client.get(reverse('grabados:api_inventario')).json()
        self.assertEqual((res['conteos']['INACTIVO'], res['conteos']['APROBADO']), (1, 1))
        res = self.client.get(reverse('grabados:api_inventario'), {'estado': 'INACTIVO'}).json()
        self.assertEqual([g['id'] for g in res['data']], [inactivo.id])
        self.assertEqual(res['data'][0]['estado_display'], 'Inactivo')
        self.assertNotEqual(activo.id, inactivo.id)

    def test_tabla_de_consultas_muestra_y_filtra_inactivos(self):
        activo = self.aprobado()
        inactivo = self.aprobado(of='22800')
        self.desactivar(inactivo)
        self.client.force_login(self.operario)
        self.assertContains(self.client.get(reverse('grabados:grabado_consulta')), '<option value="INACTIVO">')

        filas = {r['id']: r for r in self.client.get(reverse('grabados:api_consulta_registros')).json()['data']}
        self.assertEqual(filas[f'g{inactivo.id}']['estado'], 'INACTIVO')
        self.assertEqual(filas[f'g{inactivo.id}']['grabado']['estado'], 'INACTIVO')
        self.assertEqual(filas[f'g{activo.id}']['grabado']['estado'], 'APROBADO')

        res = self.client.get(reverse('grabados:api_consulta_registros'),
                              {'filtros': json.dumps([{'col': 'estado', 'in': ['INACTIVO']}])}).json()
        self.assertEqual([r['id'] for r in res['data']], [f'g{inactivo.id}'])
        valores = self.client.get(reverse('grabados:api_consulta_valores'), {'col': 'estado'}).json()
        self.assertIn('INACTIVO', json.dumps(valores))

    def test_fila_vieja_enlazada_trae_el_estado_del_grabado(self):
        grabado = self.aprobado()
        orden = OrdenFabricacion.objects.create(of='22741', proceso='STAMPING', cliente='C', estado='COMPLETADO')
        LegadoOrden.objects.create(orden=orden, rol='USO', grabado=grabado)
        self.desactivar(grabado)
        enlaces = selectors.grabados_de_ordenes([{'id': orden.id, 'of': '22741', 'referencia': None,
                                                  'proceso': 'STAMPING', 'legado_grabado': grabado.id,
                                                  'legado_rol': 'USO'}])
        self.assertEqual(enlaces[orden.id]['grabado']['estado'], 'INACTIVO')

    # --------------------------------------------------------------- API
    def post(self, nombre, grabado_id, datos=None, usuario=None):
        self.client.force_login(usuario or self.operario)
        return self.client.post(reverse(f'grabados:{nombre}', args=[grabado_id]),
                                data=json.dumps(datos or {}), content_type='application/json')

    def test_api_desactivar_y_reactivar(self):
        grabado = self.aprobado()
        respuesta = self.post('api_grabado_desactivar', grabado.id, {'motivo': 'Cliente ya no lo usa'})
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.json()['estado'], 'INACTIVO')
        grabado.refresh_from_db()
        self.assertEqual((grabado.estado, grabado.desactivado_por), ('INACTIVO', self.operario))

        respuesta = self.post('api_grabado_reactivar', grabado.id, usuario=self.supervisor)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.json()['estado'], 'APROBADO')
        self.assertIn('vuelve a "Aprobado"', respuesta.json()['message'])
        self.assertEqual(HistorialActivacion.objects.first().usuario, self.supervisor)

    def test_api_errores(self):
        grabado = self.aprobado()
        self.assertEqual(self.post('api_grabado_desactivar', grabado.id, {'motivo': ''}).status_code, 400)
        self.assertEqual(self.post('api_grabado_reactivar', grabado.id).status_code, 409)
        self.assertEqual(self.post('api_grabado_desactivar', 999999, {'motivo': 'Grabado dañado'}).status_code, 404)
        self.mandar(grabado)
        respuesta = self.post('api_grabado_desactivar', grabado.id, {'motivo': 'Grabado dañado'})
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn('recógelo', respuesta.json()['message'])
        self.assertEqual(self.client.get(reverse('grabados:api_grabado_desactivar', args=[grabado.id])).status_code, 405)

    def test_api_exige_el_permiso_del_modulo(self):
        grabado = self.aprobado()
        respuesta = self.post('api_grabado_desactivar', grabado.id, {'motivo': 'Grabado dañado'},
                              usuario=self.sin_acceso)
        self.assertEqual(respuesta.status_code, 403)
        grabado.refresh_from_db()
        self.assertEqual(grabado.estado, 'APROBADO')


class GrabadoNoSeEliminaTests(BaseGrabados):

    def test_admin_no_permite_eliminar_grabados(self):
        grabado = self.registrar()['grabado']
        modelo_admin = admin.site._registry[Grabado]
        peticion = RequestFactory().get('/')
        peticion.user = self.admin
        self.assertFalse(modelo_admin.has_delete_permission(peticion))
        self.assertFalse(modelo_admin.has_delete_permission(peticion, grabado))
        self.assertNotIn('delete_selected', modelo_admin.get_actions(peticion))
        for campo in ('desactivado_por', 'desactivado_el', 'motivo_desactivacion', 'estado_previo'):
            self.assertIn(campo, modelo_admin.get_readonly_fields(peticion, grabado))
        self.client.force_login(self.admin)
        url = reverse('admin:gestion_grabados_grabado_delete', args=[grabado.id])
        self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 403)
        self.assertTrue(Grabado.objects.filter(pk=grabado.pk).exists())

    def test_historial_de_activacion_es_de_solo_lectura_en_el_admin(self):
        modelo_admin = admin.site._registry[HistorialActivacion]
        peticion = mock.Mock(user=self.admin)
        self.assertFalse(modelo_admin.has_add_permission(peticion))
        self.assertFalse(modelo_admin.has_change_permission(peticion))
        self.assertFalse(modelo_admin.has_delete_permission(peticion))

    def test_historial_de_ubicacion_protege_al_grabado(self):
        from django.db.models import ProtectedError
        self.assertEqual(HistorialUbicacion._meta.get_field('grabado').remote_field.on_delete.__name__, 'PROTECT')
        grabado = self.registrar(tipo='DIRECTO')['grabado']
        # Sin fabricación ni K1 que también lo protejan: solo queda el historial de ubicación.
        grabado.fabricaciones.all().delete()
        servicio.cambiar_ubicacion(grabado_id=grabado.id, ubicacion='GBB-1-1', usuario=self.operario)
        with self.assertRaises(ProtectedError):
            Grabado.objects.filter(pk=grabado.pk).delete()
        self.assertEqual(HistorialUbicacion.objects.filter(grabado=grabado).count(), 1)

    def test_tipo_es_obligatorio(self):
        from django.db import IntegrityError, transaction
        from django.db.models.fields import NOT_PROVIDED
        self.assertIs(Grabado._meta.get_field('tipo').default, NOT_PROVIDED)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Grabado.objects.create(of_origen='50000', proceso='STAMPING', cliente='C')
        for tipo in ('K1', 'DIRECTO', 'LEGADO'):
            Grabado.objects.create(of_origen=f'5{len(tipo)}001', proceso='STAMPING', cliente='C', tipo=tipo)

    def test_los_servicios_pasan_el_tipo(self):
        self.assertEqual(self.registrar(of='51000', tipo='K1')['grabado'].tipo, 'K1')
        self.assertEqual(self.registrar(of='51001', tipo='DIRECTO')['grabado'].tipo, 'DIRECTO')
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'Selecciona el tipo'):
            self.registrar(of='51002', tipo=None)
        # migrar_a_grabados crea sus grabados con tipo='LEGADO' (cubierto en MigrarAGrabadosTests).
        fuente = Path(__file__).with_name('management') / 'commands' / 'migrar_a_grabados.py'
        self.assertIn("tipo='LEGADO'", fuente.read_text(encoding='utf-8'))
