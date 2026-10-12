"""
Pruebas de los modelos nuevos: Crear Grabado (K1 o directo) y decisión de K1 (fase 2),
inventario, y PLANI con envío a máquina y recogida (fase 3), y el comando
migrar_a_grabados (fase 4).

Los datos externos (API / externa_2012) se simulan con mock: las pruebas solo
usan la base `default` (test_CigarRingsEIS), que Django crea y borra sola.
"""
import json
import os
import re
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone as dt_timezone
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

import pandas as pd
from django.contrib import admin
from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import F
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.gestion_grabados import codigo128, selectors
from apps.gestion_grabados.models import (
    EnvioMaquina, EstadoBano, FabricacionGrabado, Grabado, HistorialActivacion, HistorialUbicacion,
    HistorialActivacionSobre, HistorialUbicacionSobre, LegadoOrden, Maquina, OrdenFabricacion, PruebaK1,
    Sobre, ConfiguracionEtiqueta,
)
from apps.gestion_grabados.services import grabados as servicio
from apps.gestion_grabados.services import etiquetas as servicio_etiquetas
from apps.gestion_grabados.services import sobres as servicio_sobres
from apps.gestion_grabados.views import VACIO_INFO_EXTERNA, _normalizar_of, limpiar_texto_excel

RUTA_JS = Path(__file__).parent / 'static' / 'js'

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
                  maquina_id=None, datos_manuales=None, tipo='K1', ubicacion_sobre=None, **cambios_tecnicos):
        return servicio.registrar_fabricacion(
            of_origen=of, proceso=proceso,
            info_externa=info if info is not None else info_externa(),
            normalizar_of=_normalizar_of,
            datos_tecnicos=tecnicos(**cambios_tecnicos),
            maquina_id=maquina_id if maquina_id is not None else self.maquina.id,
            datos_manuales=datos_manuales or {},
            usuario=usuario or self.operario,
            tipo=tipo,
            ubicacion_sobre=ubicacion_sobre,
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

class BaseInventario(BaseGrabados):
    """Datos y ayudas compartidos por las pruebas del Inventario (sin pruebas propias)."""

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


class InventarioGrabadosTests(BaseInventario):

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


class InventarioFiltrosColumnaTests(BaseInventario):
    """Filtros por columna del Inventario (embudo en cada encabezado), en el servidor."""

    def filtrar(self, *filtros, **params):
        return self.api(filtros=json.dumps(list(filtros)), **params)

    def test_incluir_valores(self):
        res = self.filtrar({'col': 'estado', 'in': ['APROBADO']})
        self.assertEqual((self.ofs(res), res['total']), (['22800'], 1))
        self.assertEqual(self.ofs(self.filtrar({'col': 'cliente', 'in': ['CLIENTE SA']})), ['22741', '22900'])
        self.assertEqual(self.ofs(self.filtrar({'col': 'of', 'in': ['22741', '22800']})), ['22741', '22800'])
        self.assertEqual(self.ofs(self.filtrar({'col': 'proceso', 'in': ['EMBOSSING']})), ['22800'])
        self.assertEqual(self.ofs(self.filtrar({'col': 'tipo', 'in': ['K1']})), ['22741', '22800', '22900'])
        self.assertEqual(self.ofs(self.filtrar({'col': 'referencia', 'in': ['FLOR DE LAS ANTILLAS']})), ['22800'])

    def test_excluir_valores(self):
        self.assertEqual(self.ofs(self.filtrar({'col': 'cliente', 'fuera': ['MY FATHER CIGARS']})),
                         ['22741', '22900'])
        self.assertEqual(self.ofs(self.filtrar({'col': 'estado', 'fuera': ['PENDIENTE_K1']})), ['22800'])

    def test_valor_vacio(self):
        # 22741 y 22900 no tienen ubicación: '—' los incluye (null o '').
        Grabado.objects.filter(of_origen='22900').update(ubicacion='')
        self.assertEqual(self.ofs(self.filtrar({'col': 'ubicacion', 'in': ['—']})), ['22741', '22900'])
        self.assertEqual(self.ofs(self.filtrar({'col': 'ubicacion', 'fuera': ['—']})), ['22800'])
        self.assertEqual(self.ofs(self.filtrar({'col': 'ubicacion', 'in': ['Cajón B2', '—']})),
                         ['22741', '22800', '22900'])

    def test_columnas_numericas_y_de_k1(self):
        self.assertEqual(self.ofs(self.filtrar({'col': 'intentos', 'in': ['2']})), ['22741'])
        self.assertEqual(self.ofs(self.filtrar({'col': 'intentos', 'fuera': ['2']})), ['22800', '22900'])
        self.assertEqual(self.ofs(self.filtrar({'col': 'usos', 'in': ['4']})), ['22800'])
        self.assertEqual(self.ofs(self.filtrar({'col': 'ultimo_k1', 'in': ['APROBADO']})), ['22800'])
        self.assertEqual(self.ofs(self.filtrar({'col': 'ultimo_k1', 'in': ['PENDIENTE']})), ['22741', '22900'])
        # Sin K1: 0 intentos y último K1 vacío.
        Grabado.objects.create(of_origen='25000', proceso='STAMPING', cliente='X', estado='APROBADO', tipo='LEGADO')
        self.assertEqual(self.ofs(self.filtrar({'col': 'intentos', 'in': ['0']})), ['25000'])
        self.assertEqual(self.ofs(self.filtrar({'col': 'ultimo_k1', 'in': ['—']})), ['25000'])

    def test_rango_de_fechas(self):
        hoy = timezone.localdate()
        ayer = (hoy - timedelta(days=1)).isoformat()
        self.assertEqual(self.filtrar({'col': 'creado', 'desde': hoy.isoformat()})['total'], 3)
        self.assertEqual(self.filtrar({'col': 'creado', 'hasta': ayer})['total'], 0)
        Grabado.objects.filter(of_origen='22800').update(
            actualizado_el=datetime(2026, 1, 15, 12, tzinfo=dt_timezone.utc))
        res = self.filtrar({'col': 'modificado', 'desde': '2026-01-01', 'hasta': '2026-01-31'})
        self.assertEqual(self.ofs(res), ['22800'])
        self.assertEqual(self.ofs(self.filtrar({'col': 'modificado', 'desde': hoy.isoformat()})), ['22741', '22900'])

    def test_combinado_con_busqueda_tarjetas_proceso_y_tipo(self):
        filtro = {'col': 'intentos', 'in': ['1']}
        res = self.filtrar(filtro)
        # Las tarjetas cuentan con los filtros de columna.
        self.assertEqual((res['conteos']['PENDIENTE_K1'], res['conteos']['APROBADO']), (1, 1))
        self.assertEqual(self.ofs(self.filtrar(filtro, q='cliente')), ['22900'])
        res = self.filtrar(filtro, estado='APROBADO')
        self.assertEqual((self.ofs(res), res['conteos']['PENDIENTE_K1']), (['22800'], 1))
        self.assertEqual(self.ofs(self.filtrar(filtro, proceso='STAMPING')), ['22900'])
        self.assertEqual(self.filtrar(filtro, tipo='DIRECTO')['total'], 0)
        # Varias columnas a la vez: se cumplen todas.
        self.assertEqual(self.ofs(self.filtrar(filtro, {'col': 'cliente', 'in': ['CLIENTE SA']})), ['22900'])

    def test_tope_con_filtros(self):
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_INVENTARIO', 1):
            res = self.filtrar({'col': 'cliente', 'in': ['CLIENTE SA']})
        self.assertEqual((len(res['data']), res['total'], res['limite']), (1, 2, 1))

    def test_filtros_invalidos(self):
        for filtros in ('{no es json', json.dumps([{'col': 'otra', 'in': ['x']}]),
                        json.dumps([{'col': 'usos', 'in': ['muchos']}]),
                        json.dumps([{'col': 'creado', 'desde': '15/01/2026'}]),
                        json.dumps({'col': 'estado'})):
            with self.subTest(filtros=filtros):
                respuesta = self.client.get(reverse('grabados:api_inventario'), {'filtros': filtros})
                self.assertEqual(respuesta.status_code, 400)
                self.assertEqual(respuesta.json()['status'], 'error')

    def valores(self, **params):
        respuesta = self.client.get(reverse('grabados:api_inventario_valores'), params)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return respuesta.json()

    def test_valores_de_todos_los_grabados(self):
        self.assertEqual(sorted(self.valores(col='cliente')['valores']), ['CLIENTE SA', 'MY FATHER CIGARS'])
        self.assertEqual(sorted(self.valores(col='intentos')['valores']), [1, 2])
        self.assertEqual(sorted(self.valores(col='ultimo_k1')['valores']), ['APROBADO', 'PENDIENTE'])
        estado = self.valores(col='estado')
        self.assertEqual(sorted(estado['valores']), ['APROBADO', 'PENDIENTE_K1'])
        self.assertEqual(estado['etiquetas']['PENDIENTE_K1'], 'Pendiente de K1')
        self.assertEqual(self.valores(col='cliente')['etiquetas'], {})
        # Fuera del tope de 500: los valores salen de todos los grabados.
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_INVENTARIO', 1):
            self.assertEqual(len(self.valores(col='of')['valores']), 3)

    def test_valores_respetan_la_busqueda(self):
        self.assertEqual(self.valores(col='cliente', q='father')['valores'], ['MY FATHER CIGARS'])

    def test_valores_columna_desconocida(self):
        respuesta = self.client.get(reverse('grabados:api_inventario_valores'), {'col': 'fecha'})
        self.assertEqual(respuesta.status_code, 400)

    def test_valores_requiere_login(self):
        self.client.logout()
        respuesta = self.client.get(reverse('grabados:api_inventario_valores'), {'col': 'cliente'})
        self.assertEqual(respuesta.status_code, 302)

    def test_pagina_con_embudo_en_cada_columna(self):
        respuesta = self.client.get(reverse('grabados:inventario_grabados'))
        for col in ('of', 'proceso', 'cliente', 'referencia', 'estado', 'tipo', 'intentos', 'ultimo_k1',
                    'usos', 'ubicacion', 'modificado', 'creado'):
            with self.subTest(col=col):
                self.assertContains(respuesta, f'data-columna="{col}"')
                self.assertContains(respuesta, f'id="inv-punto-{col}"')
        self.assertEqual(set(selectors.CAMPOS_INVENTARIO) | set(selectors.CAMPOS_FECHA_INVENTARIO),
                         {'of', 'proceso', 'cliente', 'referencia', 'estado', 'tipo', 'intentos', 'ultimo_k1',
                          'usos', 'ubicacion', 'modificado', 'creado'})
        self.assertContains(respuesta, 'id="filtro-columna-panel"')
        self.assertContains(respuesta, 'id="inv-quitar-columnas"')
        self.assertContains(respuesta, 'id="inv-limpiar-columna"')

    def test_usa_el_componente_compartido_de_filtros(self):
        respuesta = self.client.get(reverse('grabados:inventario_grabados'))
        self.assertContains(respuesta, 'js/componentes/filtro_columna.js')
        js = (RUTA_JS / 'inventario_grabados.js').read_text(encoding='utf-8')
        self.assertIn('window.FiltroColumna.crear', js)
        self.assertIn("api/inventario/valores/", js)

    def test_referencia_se_muestra_como_descripcion(self):
        respuesta = self.client.get(reverse('grabados:inventario_grabados'))
        self.assertContains(respuesta, 'data-nombre="Descripción"')
        self.assertContains(respuesta, 'cliente o descripción...')
        self.assertNotContains(respuesta, '<th>Referencia')
        self.assertNotContains(respuesta, 'data-nombre="Referencia"')
        js = (RUTA_JS / 'inventario_grabados.js').read_text(encoding='utf-8')
        self.assertIn('data-label="Descripción"', js)
        self.assertNotIn('data-label="Referencia"', js)
        panel = (RUTA_JS / 'componentes' / 'panel_grabado.js').read_text(encoding='utf-8')
        self.assertIn("dato('Descripción', escapar(d.referencia)", panel)
        self.assertNotIn("dato('Referencia'", panel)


class TextoLargoEnUnaLineaTests(BaseGrabados):
    """Texto largo cortado con "..." y completo en el tooltip (clase celda-texto)."""

    def test_las_cuatro_pantallas_usan_celda_texto_con_title(self):
        for archivo in ('inventario_grabados.js', 'grabados_tabla.js', 'plani_tabla.js', 'sobres.js'):
            with self.subTest(archivo=archivo):
                js = (RUTA_JS / archivo).read_text(encoding='utf-8')
                self.assertRegex(js, r'class="[^"]*celda-texto[^"]*" title="\$\{')
        css = (RUTA_JS.parent / 'css' / 'grabados_tabla.css').read_text(encoding='utf-8')
        regla = css[css.index('.celda-texto {'):]
        regla = regla[:regla.index('}')]
        for propiedad in ('text-overflow: ellipsis', 'white-space  : nowrap', 'overflow     : hidden'):
            self.assertIn(propiedad, regla)

    def test_consulta_escapa_el_title_de_la_descripcion(self):
        js = (RUTA_JS / 'grabados_tabla.js').read_text(encoding='utf-8')
        self.assertNotIn('title="${reg.desc}"', js)
        # celdaTexto escapa el title de toda celda de texto largo (descripción incluida).
        self.assertIn('title="${tituloCelda(valor)}"', js)
        self.assertIn("celdaTexto('desc', reg.desc", js)


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


class BaseConsulta(BaseGrabados):
    """Tabla de Consultas con una sola fuente: una fila por EnvioMaquina y una por cada
    grabado sin envíos; OrdenFabricacion y LegadoOrden no se leen."""

    def setUp(self):
        self.client.force_login(self.operario)

    def grabado(self, of, creado=None, tipo='DIRECTO', **cambios):
        grabado = self.registrar(of=of, tipo=tipo)['grabado']
        if creado:
            cambios['creado_el'] = datetime(creado.year, creado.month, creado.day, 12, tzinfo=dt_timezone.utc)
        if cambios:
            Grabado.objects.filter(pk=grabado.pk).update(**cambios)
        return Grabado.objects.get(pk=grabado.pk)

    def mandar(self, grabado, of, enviado=None, **fila):
        envio = servicio.mandar_a_maquina(of=of, proceso=grabado.proceso, grabado_id=grabado.id,
                                          fila=fila_excel(**fila), usuario=self.operario)
        if enviado:
            EnvioMaquina.objects.filter(pk=envio.pk).update(
                enviado_el=datetime(enviado.year, enviado.month, enviado.day, 12, tzinfo=dt_timezone.utc))
        return EnvioMaquina.objects.get(pk=envio.pk)

    def recoger(self, envio, estado='OK', ubicacion='GBB-1-1', comentario='', usuario=None):
        return servicio.recoger_de_maquina(envio_id=envio.id, of=envio.of, estado_fisico=estado,
                                           ubicacion=ubicacion, comentario=comentario,
                                           usuario=usuario or self.operario)

    def consulta(self, q='', filtros=None, esperado=200):
        params = {'q': q}
        if filtros is not None:
            params['filtros'] = json.dumps(filtros)
        respuesta = self.client.get(reverse('grabados:api_consulta_registros'), params)
        self.assertEqual(respuesta.status_code, esperado, respuesta.content)
        return respuesta.json()

    def ids(self, respuesta):
        return [r['id'] for r in respuesta['data']]

    def valores(self, **params):
        respuesta = self.client.get(reverse('grabados:api_consulta_valores'), params)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return respuesta.json()


class ConsultaUnaFuenteTests(BaseConsulta):

    # ------------------------------------------------------------ filas
    def test_una_fila_por_envio_y_una_por_grabado_sin_envio(self):
        usado = self.grabado('22741')
        primero = self.mandar(usado, '30000')
        self.recoger(primero)
        segundo = self.mandar(usado, '30001')
        nuevo = self.grabado('22800')
        res = self.consulta()
        self.assertEqual(sorted(self.ids(res)), sorted([f'e{primero.id}', f'e{segundo.id}', f'g{nuevo.id}']))
        self.assertNotIn(f'g{usado.id}', self.ids(res))   # ya tiene envíos: no sale como creación
        self.assertEqual((res['total'], res['envios'], res['creaciones'], res['limite']), (3, 2, 1, 500))
        self.assertNotIn('origen', res['data'][0])

    def test_fila_de_envio_trae_los_datos_del_envio_y_de_su_grabado(self):
        grabado = self.grabado('22741', ubicacion='GBB-1-2')
        envio = self.mandar(grabado, '30000')
        self.recoger(envio, estado='REPETIR', comentario='Rayado en el borde', usuario=self.supervisor)
        grabado.refresh_from_db()
        envio.refresh_from_db()
        fila = self.consulta()['data'][0]
        esperado = {
            'id': f'e{envio.id}', 'tipo_fila': 'ENVIO', 'of': '30000', 'of_origen': '22741',
            'cliente': 'CLIENTE SA', 'desc': 'ANILLA DORADA', 'proceso': 'STAMPING',
            'estado': 'REPETIR', 'estado_display': grabado.get_estado_display(),
            'tipo': 'DIRECTO', 'tipo_display': grabado.get_tipo_display(), 'ubicacion': 'GBB-1-1',
            'sobre': 'S-10', 'usos': 0, 'maquina': 'GIETZ 01', 'fecha': '15/10/2026',
            'cantidad': 1200, 'horas': 2.5, 'papel': 'Couché 90 g',
            'enviado_el': selectors.formatear_fecha(envio.enviado_el), 'enviado_por': 'operario',
            'recogido_el': selectors.formatear_fecha(envio.recogido_el), 'recogido_por': 'supervisor',
            'estado_fisico': 'REPETIR', 'estado_fisico_display': 'Repetir',
            'modificado': selectors.formatear_fecha(grabado.actualizado_el),
        }
        self.assertEqual({k: fila[k] for k in esperado}, esperado)
        self.assertEqual(fila['grabado'], {'id': grabado.id, 'of_origen': '22741', 'proceso': 'STAMPING',
                                           'ubicacion': 'GBB-1-1', 'estado': 'REPETIR',
                                           'actualizado_el': fila['modificado']})
        # Los datos técnicos solo van en el panel de detalle.
        for tecnico in ('peso_i', 'peso_f', 'perdida', 'temp', 'rpm', 'tiempo_t', 'comp', 'responsables'):
            self.assertNotIn(tecnico, fila)

    def test_fila_de_creacion_sin_datos_de_envio_queda_en_blanco(self):
        grabado = self.grabado('22741')
        fila = self.consulta()['data'][0]
        self.assertEqual((fila['id'], fila['tipo_fila'], fila['of'], fila['of_origen']),
                         (f'g{grabado.id}', 'CREACION', '22741', '22741'))
        for vacio in ('maquina', 'fecha', 'cantidad', 'horas', 'papel', 'enviado_el', 'enviado_por',
                      'recogido_el', 'recogido_por', 'estado_fisico', 'estado_fisico_display'):
            with self.subTest(columna=vacio):
                self.assertIsNone(fila[vacio])     # en blanco, no 0
        self.assertEqual(fila['usos'], 0)          # dato real del grabado

    def test_of_de_origen_solo_se_ve_en_envios_de_otra_of(self):
        grabado = self.grabado('7001')
        repeticion = self.mandar(grabado, '23071')            # repetición que apunta a la 7001
        self.recoger(repeticion)
        misma = self.mandar(grabado, '7001.0')                # misma OF (normalizada)
        self.recoger(misma)
        rara = self.mandar(grabado, 'SIN NUMERO')             # no se puede comparar: en blanco
        nuevo = self.grabado('22800')                         # creación
        filas = {r['id']: r for r in self.consulta()['data']}
        mostradas = {i: (f['of_origen'], f['of_origen_mostrar']) for i, f in filas.items()}
        self.assertEqual(mostradas, {
            f'e{repeticion.id}': ('7001', '7001'),
            f'e{misma.id}': ('7001', None),
            f'e{rara.id}': ('7001', None),
            f'g{nuevo.id}': ('22800', None),
        })
        # Filtro y búsqueda usan el valor real: encuentran todas las filas de la 7001.
        esperado = sorted([f'e{repeticion.id}', f'e{misma.id}', f'e{rara.id}'])
        self.assertEqual(sorted(self.ids(self.consulta(filtros=[{'col': 'of_origen', 'in': ['7001']}]))), esperado)
        self.assertEqual(sorted(self.ids(self.consulta(q='7001'))), esperado)
        self.assertEqual(sorted(self.valores(col='of_origen')['valores']), ['22800', '7001'])

    def test_maquina_sin_maquina_legado_en_blanco_pero_filtrable(self):
        from apps.gestion_grabados.management.commands.migrar_a_grabados import MAQUINA_SIN_NOMBRE
        legado = Maquina.objects.create(nombre=MAQUINA_SIN_NOMBRE)
        grabado = self.grabado('22741')
        envio_legado = self.mandar(grabado, '30000')
        EnvioMaquina.objects.filter(pk=envio_legado.pk).update(maquina=legado)
        self.recoger(envio_legado)
        envio_gietz = self.mandar(grabado, '30001')
        filas = {r['id']: r for r in self.consulta()['data']}
        self.assertEqual((filas[f'e{envio_legado.id}']['maquina'], filas[f'e{envio_legado.id}']['maquina_mostrar']),
                         (MAQUINA_SIN_NOMBRE, None))
        self.assertEqual(filas[f'e{envio_gietz.id}']['maquina_mostrar'], 'GIETZ 01')
        self.assertIn(MAQUINA_SIN_NOMBRE, self.valores(col='maquina')['valores'])
        res = self.consulta(filtros=[{'col': 'maquina', 'in': [MAQUINA_SIN_NOMBRE]}])
        self.assertEqual(self.ids(res), [f'e{envio_legado.id}'])
        # La celda y su tooltip usan lo que se ve ("—" sin valor), no el nombre real.
        js = (RUTA_JS / 'grabados_tabla.js').read_text(encoding='utf-8')
        self.assertIn("celdaTexto('maquina', conRaya(reg.maquina_mostrar)", js)
        self.assertIn("celdaTexto('of_origen', conRaya(reg.of_origen_mostrar)", js)
        self.assertIn("return valor === null || valor === undefined || valor === '' ? VACIO : valor;", js)
        self.assertIn("const VACIO = '—';", js)
        self.assertIn("mostrar: 'of_origen_mostrar'", js)
        self.assertIn("mostrar: 'maquina_mostrar'", js)

    def test_envio_sin_cantidad_ni_horas_queda_en_blanco(self):
        grabado = self.grabado('22741')
        self.mandar(grabado, '30000', cantidad_formatos=None, horas_proceso=None, papel=None, fecha_programada=None)
        fila = self.consulta()['data'][0]
        self.assertEqual((fila['cantidad'], fila['horas'], fila['papel'], fila['fecha']), (None, None, None, None))

    def test_orden_por_fecha_de_envio_o_creacion_con_tope(self):
        viejo = self.grabado('100', creado=date(2026, 1, 1))
        envio_viejo = self.mandar(viejo, '30000', enviado=date(2026, 8, 1))
        reciente = self.grabado('101', creado=date(2026, 9, 1))
        envio_reciente = self.mandar(reciente, '30001', enviado=date(2026, 10, 1))
        creado_medio = self.grabado('102', creado=date(2026, 9, 15))
        creado_viejo = self.grabado('103', creado=date(2025, 1, 1))
        res = self.consulta()
        self.assertEqual(self.ids(res), [f'e{envio_reciente.id}', f'g{creado_medio.id}', f'e{envio_viejo.id}',
                                         f'g{creado_viejo.id}'])
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_CONSULTA', 2):
            res = self.consulta()
        self.assertEqual(self.ids(res), [f'e{envio_reciente.id}', f'g{creado_medio.id}'])
        self.assertEqual((res['total'], res['envios'], res['creaciones'], res['limite']), (4, 2, 2, 2))

    def test_ordenes_viejas_no_se_muestran_ni_se_tocan(self):
        # OF 9408: SIN_MIGRAR, pendiente y sin datos técnicos.
        sin_migrar = OrdenFabricacion.objects.create(of='9408', proceso='STAMPING', estado='PENDIENTE',
                                                     cliente='CLIENTE VIEJO')
        LegadoOrden.objects.create(orden=sin_migrar, rol='SIN_MIGRAR', grabado=None)
        grabado = self.grabado('22741')
        migrada = OrdenFabricacion.objects.create(of='22741', proceso='STAMPING', estado='COMPLETADO',
                                                  ubicacion='Cajón viejo')
        LegadoOrden.objects.create(orden=migrada, rol='FABRICACION', grabado=grabado)
        antes = (list(OrdenFabricacion.objects.order_by('id').values()),
                 list(LegadoOrden.objects.order_by('id').values()))

        res = self.consulta()
        self.assertEqual((self.ids(res), res['total']), ([f'g{grabado.id}'], 1))   # la migrada, por su grabado
        self.assertEqual(self.consulta(q='9408')['total'], 0)
        self.assertNotIn('9408', self.valores(col='of')['valores'])
        self.assertNotIn('CLIENTE VIEJO', self.valores(col='cliente')['valores'])
        self.client.get(reverse('grabados:api_kpis'))
        self.client.post(reverse('grabados:api_grabado_ubicacion', kwargs={'grabado_id': grabado.id}),
                         data=json.dumps({'ubicacion': 'GBB-9-9', 'origen': 'CONSULTA'}),
                         content_type='application/json')

        self.assertEqual((list(OrdenFabricacion.objects.order_by('id').values()),
                          list(LegadoOrden.objects.order_by('id').values())), antes)

    # ------------------------------------------------------- búsqueda
    def test_busqueda_por_of_del_envio_origen_cliente_y_maquina(self):
        grabado = self.grabado('22741', cliente='TABACALERA DEL CIBAO', ubicacion='GBB-4-4')
        envio = self.mandar(grabado, '30500')
        otro = self.grabado('22800', cliente='OTRO SA')
        self.assertEqual(self.ids(self.consulta(q='30500')), [f'e{envio.id}'])
        for q in ('22741', 'cibao', 'gbb-4', 'gietz', 'tabacalera stamping'):
            with self.subTest(q=q):
                self.assertEqual(self.ids(self.consulta(q=q)), [f'e{envio.id}'])
        self.assertEqual(self.ids(self.consulta(q='otro')), [f'g{otro.id}'])
        self.assertEqual(self.consulta(q='nada')['total'], 0)

    def test_busqueda_mira_todo_el_historial_con_tope(self):
        viejo = self.grabado('399', creado=date(2020, 1, 1))
        for i in range(3):
            self.grabado(str(300 + i), creado=date(2026, 10, 1 + i))
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_CONSULTA', 2):
            self.assertNotIn(f'g{viejo.id}', self.ids(self.consulta()))
            res = self.consulta(q='399')
        self.assertEqual((self.ids(res), res['total']), ([f'g{viejo.id}'], 1))

    # -------------------------------------------------------- filtros
    def test_filtros_por_columna(self):
        usado = self.grabado('22741', cliente='USADO SA')
        envio = self.mandar(usado, '30000')
        self.recoger(envio)
        nuevo = self.grabado('22800', cliente='NUEVO SA', tipo='K1')
        e, g = f'e{envio.id}', f'g{nuevo.id}'
        casos = [
            ([{'col': 'cliente', 'in': ['NUEVO SA']}], [g]),
            ([{'col': 'cliente', 'fuera': ['NUEVO SA']}], [e]),
            ([{'col': 'of', 'in': ['30000']}], [e]),
            ([{'col': 'of_origen', 'in': ['22741']}], [e]),
            ([{'col': 'estado', 'in': ['PENDIENTE_K1']}], [g]),
            ([{'col': 'tipo', 'in': ['DIRECTO']}], [e]),
            ([{'col': 'estado_fisico', 'in': ['OK']}], [e]),
            ([{'col': 'enviado_por', 'in': ['operario']}], [e]),
            ([{'col': 'cantidad', 'in': ['1200']}], [e]),
            ([{'col': 'horas', 'in': ['2.5']}], [e]),
            ([{'col': 'usos', 'in': ['1']}], [e]),
            # Lo que la fila de creación no tiene: vacío ('—').
            ([{'col': 'maquina', 'in': ['—']}], [g]),
            ([{'col': 'maquina', 'fuera': ['—']}], [e]),
            ([{'col': 'cantidad', 'in': ['—']}], [g]),
            # Varios filtros se cumplen todos (aunque repitan columna).
            ([{'col': 'estado', 'in': ['APROBADO']}, {'col': 'estado', 'in': ['REPETIR']}], []),
        ]
        for filtros, esperado in casos:
            with self.subTest(filtros=filtros):
                self.assertEqual(self.ids(self.consulta(filtros=filtros)), esperado)

    def test_boton_fecha_y_rangos_de_fecha(self):
        grabado = self.grabado('22741', creado=date(2026, 9, 1))
        envio = self.mandar(grabado, '30000', enviado=date(2026, 9, 20))
        nuevo = self.grabado('22800', creado=date(2026, 9, 10))
        # Fecha prog. (botón Fecha): la fila de creación no tiene y no pasa.
        res = self.consulta(filtros=[{'col': 'fecha', 'desde': '2026-10-01', 'hasta': '2026-10-31'}])
        self.assertEqual(self.ids(res), [f'e{envio.id}'])
        self.assertEqual(self.consulta(filtros=[{'col': 'fecha', 'desde': '2026-11-01'}])['total'], 0)
        res = self.consulta(filtros=[{'col': 'enviado_el', 'desde': '2026-09-15', 'hasta': '2026-09-30'}])
        self.assertEqual(self.ids(res), [f'e{envio.id}'])
        hoy = timezone.localdate().isoformat()
        res = self.consulta(filtros=[{'col': 'modificado', 'desde': hoy, 'hasta': hoy}])
        self.assertEqual(sorted(self.ids(res)), sorted([f'e{envio.id}', f'g{nuevo.id}']))
        self.assertEqual(self.consulta(filtros=[{'col': 'modificado', 'hasta': '2020-01-01'}])['total'], 0)

    def test_filtros_invalidos(self):
        for filtros in ({'cliente': ['X']}, [{'col': 'nada', 'in': ['X']}], [{'col': 'origen', 'in': ['Nuevo']}],
                        [{'col': 'temp', 'in': ['150']}], [{'col': 'cantidad', 'in': ['abc']}],
                        [{'col': 'fecha', 'desde': '10/01/2026'}], [{'col': ['of'], 'in': []}],
                        [{'col': 'of', 'in': ['1'] * (selectors.MAX_VALORES_FILTRO + 1)}]):
            with self.subTest(filtros=filtros):
                self.assertEqual(self.consulta(filtros=filtros, esperado=400)['status'], 'error')
        respuesta = self.client.get(reverse('grabados:api_consulta_registros'), {'filtros': '{malo'})
        self.assertEqual(respuesta.status_code, 400)

    def test_valores_de_columna_juntan_envios_y_creaciones(self):
        usado = self.grabado('22741', cliente='USADO SA')
        self.mandar(usado, '30000')
        self.grabado('22800', cliente='NUEVO SA')
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_CONSULTA', 1):
            self.assertEqual(sorted(self.valores(col='cliente')['valores']), ['NUEVO SA', 'USADO SA'])
        self.assertEqual(self.valores(col='cliente', q='22800')['valores'], ['NUEVO SA'])
        self.assertEqual(sorted(self.valores(col='of')['valores']), ['22800', '30000'])
        maquinas = self.valores(col='maquina')['valores']
        self.assertIn(None, maquinas)              # la fila de creación no tiene máquina
        self.assertIn('GIETZ 01', maquinas)
        estado = self.valores(col='estado')
        self.assertEqual(sorted(estado['valores']), ['APROBADO', 'EN_MAQUINA'])
        self.assertEqual(estado['etiquetas']['EN_MAQUINA'], dict(Grabado.ESTADO_CHOICES)['EN_MAQUINA'])
        self.assertEqual(self.valores(col='cliente')['etiquetas'], {})
        self.assertEqual(self.client.get(reverse('grabados:api_consulta_valores'), {'col': 'origen'}).status_code, 400)

    # -------------------------------------------- panel y ubicación
    def test_ubicacion_se_edita_con_cambiar_ubicacion_en_todas_las_filas_del_grabado(self):
        grabado = self.grabado('22741', ubicacion='GBB-1-1')
        self.recoger(self.mandar(grabado, '30000'))
        self.mandar(grabado, '30001')
        url = reverse('grabados:api_grabado_ubicacion', kwargs={'grabado_id': grabado.id})
        respuesta = self.client.post(url, data=json.dumps({'ubicacion': 'gbb-2-3', 'origen': 'CONSULTA'}),
                                     content_type='application/json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        cambio = HistorialUbicacion.objects.filter(grabado=grabado).latest('id')
        self.assertEqual((cambio.ubicacion_nueva, cambio.origen, cambio.usuario), ('GBB-2-3', 'CONSULTA', self.operario))
        filas = self.consulta()['data']
        self.assertEqual(len(filas), 2)
        for fila in filas:
            self.assertEqual((fila['ubicacion'], fila['grabado']['ubicacion'], fila['modificado']),
                             ('GBB-2-3', 'GBB-2-3', respuesta.json()['actualizado_el']))

    def test_inactivo_se_muestra_y_se_filtra(self):
        activo = self.grabado('22741')
        inactivo = self.grabado('22800')
        servicio.desactivar_grabado(grabado_id=inactivo.id, motivo='Pieza dañada', usuario=self.operario)
        self.assertContains(self.client.get(reverse('grabados:grabado_consulta')), '<option value="INACTIVO">')
        filas = {r['id']: r for r in self.consulta()['data']}
        self.assertEqual((filas[f'g{inactivo.id}']['estado'], filas[f'g{inactivo.id}']['grabado']['estado']),
                         ('INACTIVO', 'INACTIVO'))
        self.assertEqual(filas[f'g{activo.id}']['grabado']['estado'], 'APROBADO')
        res = self.consulta(filtros=[{'col': 'estado', 'in': ['INACTIVO']}])
        self.assertEqual(self.ids(res), [f'g{inactivo.id}'])
        self.assertIn('INACTIVO', self.valores(col='estado')['valores'])

    def test_pantalla_sin_origen_ni_editar_ni_eliminar(self):
        html = self.client.get(reverse('grabados:grabado_consulta')).content.decode()
        for fuera in ('data-col="origen"', 'Sistema anterior', 'USER_IS_ADMIN', '(nuevo)'):
            with self.subTest(fuera=fuera):
                self.assertNotIn(fuera, html)
        self.assertIn('id="tabla-encabezado"', html)
        js = (RUTA_JS / 'grabados_tabla.js').read_text(encoding='utf-8')
        for fuera in ("'origen'", 'Sistema anterior', 'eliminarRegistro', 'editarRegistro', '/api/eliminar/',
                      'abrirOrden', 'ordenfabricacion', 'setMonth('):
            with self.subTest(fuera=fuera):
                self.assertNotIn(fuera, js)
        self.assertIn('window.PanelGrabado.abrir(reg.grabado.id)', js)
        self.assertIn('/grabados/api/consulta/registros/', js)

    def test_columnas_por_defecto_y_opcionales(self):
        js = (RUTA_JS / 'grabados_tabla.js').read_text(encoding='utf-8')
        por_defecto = ['of', 'of_origen', 'cliente', 'desc', 'proceso', 'estado', 'maquina', 'ubicacion',
                       'fecha', 'modificado']
        opcionales = ['sobre', 'cantidad', 'horas', 'papel', 'enviado_el', 'enviado_por', 'recogido_el',
                      'recogido_por', 'estado_fisico', 'tipo', 'usos']
        columnas = re.findall(r"\{ id: '(\w+)',([^}]*)\}", js)
        self.assertEqual([c for c, resto in columnas if 'defecto: true' in resto], por_defecto)
        self.assertEqual([c for c, resto in columnas if 'defecto: true' not in resto], opcionales)
        # Toda columna de la pantalla se puede filtrar en el servidor.
        self.assertEqual(set(selectors.CAMPOS_CONSULTA) | set(selectors.CAMPOS_FECHA_CONSULTA),
                         set(por_defecto) | set(opcionales))

    # ------------------------------------------------------- tarjetas
    def test_tarjetas_cuadran_con_el_inventario(self):
        en_maquina = self.grabado('70000')
        self.mandar(en_maquina, '71000')
        self.grabado('70001', estado='REPETIR')
        self.grabado('70002', estado='EN_FABRICACION')
        self.registrar(of='70003', tipo='K1')                      # PENDIENTE_K1
        self.grabado('70004')                                      # APROBADO: no cuenta
        # Filas viejas sin migrar: ya no cuentan.
        for i, estado in enumerate(['EN_MAQUINA', 'REPETIR', 'PENDIENTE', 'COMPLETADO']):
            OrdenFabricacion.objects.create(of=str(80000 + i), proceso='STAMPING', estado=estado)

        kpis = self.client.get(reverse('grabados:api_kpis')).json()
        conteos = self.client.get(reverse('grabados:api_inventario')).json()['conteos']
        self.assertEqual(kpis['en_maquina'], conteos['EN_MAQUINA'])
        self.assertEqual(kpis['para_repetir'], conteos['REPETIR'])
        self.assertEqual(kpis['pendientes'], conteos['EN_FABRICACION'] + conteos['PENDIENTE_K1'])
        self.assertEqual((kpis['en_maquina'], kpis['para_repetir'], kpis['pendientes'], kpis['completados_hoy']),
                         (1, 1, 2, 0))

    def test_terminados_hoy_son_los_envios_recogidos_hoy(self):
        hoy = self.grabado('70000')
        self.recoger(self.mandar(hoy, '71000'))
        repetir = self.grabado('70001')
        self.recoger(self.mandar(repetir, '71001'), estado='REPETIR', comentario='Rayado en el borde')
        ayer = self.grabado('70002')
        envio_ayer = self.mandar(ayer, '71002')
        self.recoger(envio_ayer)
        EnvioMaquina.objects.filter(pk=envio_ayer.pk).update(recogido_el=timezone.now() - timedelta(days=1))
        self.mandar(self.grabado('70003'), '71003')                # abierto: no cuenta
        self.assertEqual(self.client.get(reverse('grabados:api_kpis')).json()['completados_hoy'], 2)

    # ------------------------------------------- respaldo y Estadísticas
    def test_eliminar_ordenes_viejas_retirado(self):
        OrdenFabricacion.objects.create(of='22741', proceso='STAMPING')
        self.client.force_login(self.admin)
        respuesta = self.client.post(reverse('grabados:api_eliminar'),
                                     data=json.dumps({'of': '22741', 'proceso': 'STAMPING'}),
                                     content_type='application/json')
        self.assertEqual(respuesta.status_code, 410)
        self.assertEqual(respuesta.json()['status'], 'error')
        self.assertTrue(OrdenFabricacion.objects.filter(of='22741').exists())

    def test_estadisticas_siguen_leyendo_ordenes_viejas(self):
        for i in range(3):
            OrdenFabricacion.objects.create(of=str(80000 + i), proceso='STAMPING', estado='COMPLETADO')
        self.grabado('22741')
        with mock.patch('apps.gestion_grabados.selectors.LIMITE_CONSULTA', 1):
            datos = self.client.get(reverse('grabados:api_registros')).json()
        self.assertEqual(sorted(r['of'] for r in datos), ['80000', '80001', '80002'])


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
        # El PLANI imprime la etiqueta de este grabado después de guardar la ubicación.
        self.assertEqual(respuesta.json()['grabado_id'], grabado.id)

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

    def test_pagina_plani_con_filtros_por_columna(self):
        respuesta = self.client.get(reverse('grabados:plani_consulta'))
        for col in ('of', 'ref', 'descripcion', 'cliente', 'proceso', 'maquina', 'fecha', 'grabado'):
            with self.subTest(col=col):
                self.assertContains(respuesta, f'data-columna="{col}"')
                self.assertContains(respuesta, f'id="plani-punto-{col}"')
        for id_html in ('filtro-columna-panel', 'plani-quitar-columnas', 'plani-limpiar-columna'):
            self.assertContains(respuesta, f'id="{id_html}"')
        self.assertContains(respuesta, 'js/componentes/filtro_columna.js')
        # Las filas vienen del Excel: se filtran en la pantalla, sin endpoint de valores.
        js = (RUTA_JS / 'plani_tabla.js').read_text(encoding='utf-8')
        self.assertIn('window.FiltroColumna.crear', js)
        self.assertIn("columnasFecha: ['fecha']", js)
        self.assertContains(respuesta, 'Busca por OF de origen, cliente o descripción...')   # buscador de "Usar otro grabado"

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
               'grabado_consulta', 'grabado_estadisticas', 'sobres', 'consultor_of')

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

    def test_eliminar_sin_permiso_del_modulo_no_entra(self):
        OrdenFabricacion.objects.create(of='22741', proceso='STAMPING')
        url = reverse('grabados:api_eliminar')
        staff_sin_modulo = User.objects.create_user('staff_sin_modulo', password='x', is_staff=True)
        self.client.force_login(staff_sin_modulo)
        self.assertEqual(self.client.post(url, data='{}', content_type='application/json').status_code, 403)
        self.client.force_login(self.operario)
        self.assertEqual(self.client.post(url, data='{}', content_type='application/json').status_code, 410)
        self.assertTrue(OrdenFabricacion.objects.exists())

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
MODELOS_NUEVOS = (Grabado, FabricacionGrabado, EnvioMaquina, Maquina, LegadoOrden, Sobre)


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

    def test_crea_el_sobre_de_cada_grabado_migrado(self):
        self.orden('22741', sobre='10567.0')
        self.orden('22800', proceso='EMBOSSING', sobre=None)
        salida = self.migrar('--aplicar')

        for grabado in Grabado.objects.select_related('sobre_asociado'):
            sobre = grabado.sobre_asociado
            self.assertIsNotNone(sobre, grabado)
            self.assertEqual((sobre.of_origen, sobre.proceso), (grabado.of_origen, grabado.proceso))
            self.assertIsNone(sobre.ubicacion)
            self.assertEqual(sobre.estado, 'ACTIVO')
        self.assertEqual(Sobre.objects.get(of_origen='22741').codigo, '10567')
        self.assertEqual(Sobre.objects.get(of_origen='22800').codigo, '')
        self.assertRegex(salida, r'Sobres creados\s+2')

    def test_grabado_reutilizado_sin_sobre_recibe_el_suyo(self):
        existente = Grabado.objects.create(of_origen='22741', proceso='STAMPING', cliente='ALTA',
                                           estado='APROBADO', tipo='K1', sobre='S-1')
        self.orden('22741')
        self.migrar('--aplicar')
        existente.refresh_from_db()
        self.assertEqual(existente.sobre_asociado.codigo, 'S-1')
        self.assertEqual(Sobre.objects.count(), 1)


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


# ============================================================
# SOBRES: modelo, migración de datos, servicios, pantalla y admin
# ============================================================

class LimpiarCodigoSobreTests(TestCase):

    CASOS = [
        ('10567.0', '10567'), ('10567.00', '10567'), (' 10567 ', '10567'), ('10567', '10567'),
        ('10567.5', '10567.5'), ('Caja  B2', 'Caja B2'), ('S-10', 'S-10'), ('', ''), (None, ''), (10567, '10567'),
    ]

    def test_limpia_el_codigo(self):
        for valor, esperado in self.CASOS:
            with self.subTest(valor=valor):
                self.assertEqual(servicio_sobres.limpiar_codigo_sobre(valor), esperado)

    def test_la_migracion_usa_la_misma_regla(self):
        import importlib
        migracion = importlib.import_module('apps.gestion_grabados.migrations.0026_sobres_existentes')
        for valor, esperado in self.CASOS:
            with self.subTest(valor=valor):
                self.assertEqual(migracion.limpiar_codigo(valor), esperado)


class MigracionSobresExistentesTests(TestCase):
    """migrations/0026_sobres_existentes.crear_sobres sobre grabados ya guardados."""

    FECHA_VIEJA = datetime(2026, 1, 15, 10, 0, tzinfo=dt_timezone.utc)

    def crear_sobres(self):
        import importlib
        from django.apps import apps as registro
        importlib.import_module('apps.gestion_grabados.migrations.0026_sobres_existentes').crear_sobres(registro, None)

    def grabado(self, of, sobre, proceso='STAMPING'):
        grabado = Grabado.objects.create(of_origen=of, proceso=proceso, cliente='C', referencia='R',
                                         tipo='LEGADO', estado='APROBADO', sobre=sobre, ubicacion='GBB-1-1')
        Grabado.objects.filter(pk=grabado.pk).update(actualizado_el=self.FECHA_VIEJA)
        return grabado

    def test_crea_un_sobre_por_grabado_sin_cambiar_el_grabado(self):
        self.grabado('10567', '10567.0')
        self.grabado('10567', ' 10567.0 ', proceso='EMBOSSING')
        self.grabado('20000', 'Caja B2')
        self.grabado('30000', '')
        self.grabado('40000', None)
        antes = {g['id']: g for g in Grabado.objects.values()}

        self.crear_sobres()

        self.assertEqual(Sobre.objects.count(), 5)
        for grabado in Grabado.objects.select_related('sobre_asociado'):
            sobre = grabado.sobre_asociado
            self.assertEqual((sobre.of_origen, sobre.proceso), (grabado.of_origen, grabado.proceso))
            self.assertEqual((sobre.ubicacion, sobre.estado, sobre.creado_por), (None, 'ACTIVO', None))
        codigos = dict(Sobre.objects.filter(proceso='STAMPING').values_list('of_origen', 'codigo'))
        self.assertEqual(codigos, {'10567': '10567', '20000': 'Caja B2', '30000': '', '40000': ''})
        self.assertEqual(Sobre.objects.get(of_origen='10567', proceso='EMBOSSING').codigo, '10567')

        # Ningún dato del grabado cambia (ni actualizado_el): solo el enlace al sobre.
        for g in Grabado.objects.values():
            self.assertIsNotNone(g.pop('sobre_asociado_id'))
            previo = dict(antes[g['id']])
            previo.pop('sobre_asociado_id')
            self.assertEqual(g, previo)
            self.assertEqual(g['actualizado_el'], self.FECHA_VIEJA)

    def test_se_puede_repetir_sin_duplicar(self):
        self.grabado('10567', '10567.0')
        self.crear_sobres()
        enlace = Grabado.objects.get().sobre_asociado_id
        self.grabado('20000', 'S-2')   # grabado nuevo entre corridas
        self.crear_sobres()
        self.crear_sobres()
        self.assertEqual(Sobre.objects.count(), 2)
        self.assertEqual(Grabado.objects.get(of_origen='10567').sobre_asociado_id, enlace)

    def test_reutiliza_el_sobre_que_ya_existe_para_la_of_y_proceso(self):
        existente = Sobre.objects.create(of_origen='10567', proceso='STAMPING', codigo='OTRO')
        self.grabado('10567', '10567.0')
        self.crear_sobres()
        self.assertEqual(Sobre.objects.count(), 1)
        self.assertEqual(Grabado.objects.get().sobre_asociado, existente)
        existente.refresh_from_db()
        self.assertEqual(existente.codigo, 'OTRO')

    def test_sin_grabados_no_hace_nada(self):
        self.crear_sobres()
        self.assertFalse(Sobre.objects.exists())


class SobreAlCrearGrabadoTests(BaseGrabados):

    def test_alta_crea_y_enlaza_el_sobre(self):
        grabado = self.registrar(info=info_externa(sobre='10567.0'))['grabado']
        grabado.refresh_from_db()
        sobre = grabado.sobre_asociado
        self.assertEqual((sobre.of_origen, sobre.proceso, sobre.codigo), ('22741', 'STAMPING', '10567'))
        self.assertEqual(grabado.sobre, '10567.0')   # el texto del grabado queda como está
        self.assertIsNone(sobre.ubicacion)
        self.assertEqual((sobre.estado, sobre.creado_por), ('ACTIVO', self.operario))
        self.assertFalse(HistorialUbicacionSobre.objects.exists())

    def test_alta_directa_sin_sobre_en_la_api_crea_el_sobre_sin_codigo(self):
        grabado = self.registrar(tipo='DIRECTO', info=info_externa(sobre=None))['grabado']
        grabado.refresh_from_db()
        self.assertEqual(grabado.sobre_asociado.codigo, '')

    def test_alta_con_ubicacion_del_sobre(self):
        grabado = self.registrar(ubicacion_sobre=' gbb-2 -1 ')['grabado']
        sobre = Sobre.objects.get(grabado=grabado)
        self.assertEqual(sobre.ubicacion, 'GBB-2-1')
        historial = HistorialUbicacionSobre.objects.get()
        self.assertEqual((historial.sobre, historial.ubicacion_anterior, historial.ubicacion_nueva,
                          historial.origen, historial.usuario),
                         (sobre, None, 'GBB-2-1', 'ALTA', self.operario))

    def test_ubicacion_del_sobre_invalida_no_guarda_nada(self):
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'no es válida'):
            self.registrar(ubicacion_sobre='Cajón B2')
        for modelo in (Grabado, Sobre, FabricacionGrabado, PruebaK1, HistorialUbicacionSobre):
            self.assertFalse(modelo.objects.exists(), modelo.__name__)

    def test_refabricacion_no_crea_otro_sobre(self):
        grabado = self.registrar(tipo='DIRECTO')['grabado']
        Grabado.objects.filter(pk=grabado.pk).update(estado='REPETIR')
        resultado = self.registrar(tipo=None, ubicacion_sobre='GBB-9-9')
        self.assertEqual(resultado['accion'], servicio.ACCION_REPETICION)
        self.assertEqual(Sobre.objects.count(), 1)
        self.assertIsNone(Sobre.objects.get().ubicacion)

    def test_vista_registrar_pasa_la_ubicacion_del_sobre(self):
        self.client.force_login(self.operario)
        with mock.patch(RUTA_BUSCAR_EXTERNOS, return_value=info_externa()):
            respuesta = self.client.post(
                reverse('grabados:api_alta_registrar'),
                json.dumps({'of': '22741', 'proceso': 'STAMPING', 'tipo': 'DIRECTO', 'tecnicos': tecnicos(),
                            'ubicacion_sobre': 'gbb-3-4'}),
                content_type='application/json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(Sobre.objects.get().ubicacion, 'GBB-3-4')

    def test_pantalla_crear_grabado_tiene_el_campo(self):
        self.client.force_login(self.operario)
        respuesta = self.client.get(reverse('grabados:alta_grabado'))
        self.assertContains(respuesta, 'id="alta-ubicacion-sobre"')
        self.assertContains(respuesta, 'Ubicación del sobre:')


class SobreServiciosTests(BaseGrabados):

    def setUp(self):
        self.grabado = self.registrar(tipo='DIRECTO')['grabado']
        self.grabado.refresh_from_db()
        self.sobre = self.grabado.sobre_asociado

    def cambiar(self, ubicacion, sobre_id=None):
        return servicio_sobres.cambiar_ubicacion_sobre(sobre_id=sobre_id or self.sobre.id, ubicacion=ubicacion,
                                                       usuario=self.operario)

    def test_cambiar_ubicacion_normaliza_y_deja_historial(self):
        self.assertTrue(self.cambiar(' gbb-2-1 ').ubicacion_cambio)
        self.cambiar('GAB-1-12')
        self.sobre.refresh_from_db()
        self.assertEqual(self.sobre.ubicacion, 'GAB-1-12')
        self.assertEqual(list(HistorialUbicacionSobre.objects.order_by('id')
                              .values_list('ubicacion_anterior', 'ubicacion_nueva', 'origen')),
                         [(None, 'GBB-2-1', 'SOBRES'), ('GBB-2-1', 'GAB-1-12', 'SOBRES')])

    def test_misma_ubicacion_no_escribe(self):
        self.cambiar('GBB-2-1')
        self.assertFalse(self.cambiar('gbb-2-1').ubicacion_cambio)
        self.assertEqual(HistorialUbicacionSobre.objects.count(), 1)

    def test_ubicacion_invalida_o_vacia(self):
        for valor in ('', 'Cajón B2', 'G-1-1', 'GBB-1234-1'):
            with self.subTest(valor=valor), self.assertRaises(servicio.ErrorGrabado):
                self.cambiar(valor)
        self.assertFalse(HistorialUbicacionSobre.objects.exists())

    def test_sobre_inexistente(self):
        with self.assertRaises(servicio_sobres.SobreNoExiste) as ctx:
            self.cambiar('GBB-1-1', sobre_id=999999)
        self.assertEqual(ctx.exception.status, 404)

    def test_desactivar_y_reactivar(self):
        with self.assertRaisesMessage(servicio.ErrorGrabado, 'motivo'):
            servicio_sobres.desactivar_sobre(sobre_id=self.sobre.id, motivo='mal', usuario=self.operario)

        sobre = servicio_sobres.desactivar_sobre(sobre_id=self.sobre.id, motivo='Sobre extraviado',
                                                 usuario=self.operario)
        sobre.refresh_from_db()
        self.assertEqual((sobre.estado, sobre.desactivado_por, sobre.motivo_desactivacion),
                         ('INACTIVO', self.operario, 'Sobre extraviado'))
        self.assertIsNotNone(sobre.desactivado_el)
        with self.assertRaises(servicio.TransicionInvalida):
            servicio_sobres.desactivar_sobre(sobre_id=sobre.id, motivo='Otra vez', usuario=self.operario)
        with self.assertRaisesMessage(servicio.TransicionInvalida, 'inactivo'):
            self.cambiar('GBB-1-1')

        sobre = servicio_sobres.reactivar_sobre(sobre_id=sobre.id, usuario=self.supervisor)
        sobre.refresh_from_db()
        self.assertEqual((sobre.estado, sobre.desactivado_por, sobre.desactivado_el, sobre.motivo_desactivacion),
                         ('ACTIVO', None, None, ''))
        with self.assertRaises(servicio.TransicionInvalida):
            servicio_sobres.reactivar_sobre(sobre_id=sobre.id, usuario=self.operario)

        self.assertEqual(list(HistorialActivacionSobre.objects.order_by('id')
                              .values_list('accion', 'motivo', 'usuario__username')),
                         [('DESACTIVAR', 'Sobre extraviado', 'operario'), ('REACTIVAR', '', 'supervisor')])

    def test_sobre_y_grabado_son_independientes(self):
        servicio_sobres.desactivar_sobre(sobre_id=self.sobre.id, motivo='Sobre extraviado', usuario=self.operario)
        self.grabado.refresh_from_db()
        self.assertEqual(self.grabado.estado, 'APROBADO')

        servicio_sobres.reactivar_sobre(sobre_id=self.sobre.id, usuario=self.operario)
        servicio.desactivar_grabado(grabado_id=self.grabado.id, motivo='Grabado roto', usuario=self.operario)
        self.assertEqual(self.cambiar('GBB-5-5').ubicacion, 'GBB-5-5')
        self.sobre.refresh_from_db()
        self.assertEqual(self.sobre.estado, 'ACTIVO')

    def test_cambios_del_sobre_no_tocan_el_grabado(self):
        antes = Grabado.objects.values().get(pk=self.grabado.pk)
        self.cambiar('GBB-1-1')
        servicio_sobres.desactivar_sobre(sobre_id=self.sobre.id, motivo='Sobre extraviado', usuario=self.operario)
        self.assertEqual(Grabado.objects.values().get(pk=self.grabado.pk), antes)

    def test_el_sobre_no_se_puede_borrar(self):
        from django.db.models import ProtectedError
        self.cambiar('GBB-1-1')
        with self.assertRaises(ProtectedError):
            Sobre.objects.filter(pk=self.sobre.pk).delete()
        self.assertTrue(Sobre.objects.filter(pk=self.sobre.pk).exists())


class SobresPantallaTests(BaseGrabados):

    def setUp(self):
        self.client.force_login(self.operario)

    def sobre(self, of, codigo='', ubicacion=None, proceso='STAMPING'):
        return Sobre.objects.create(of_origen=of, proceso=proceso, codigo=codigo, ubicacion=ubicacion)

    def listar(self, q=''):
        respuesta = self.client.get(reverse('grabados:api_sobres'), {'q': q})
        self.assertEqual(respuesta.status_code, 200)
        return respuesta.json()

    def post(self, nombre, sobre_id, datos=None):
        return self.client.post(reverse(f'grabados:{nombre}', args=[sobre_id]), json.dumps(datos or {}),
                                content_type='application/json')

    def test_pagina_y_menu(self):
        respuesta = self.client.get(reverse('grabados:sobres'))
        self.assertContains(respuesta, 'Sobres | CIGAR RINGS')
        self.assertContains(respuesta, '>Sobres</a')
        self.assertContains(respuesta, 'js/sobres.js')

    def test_lista_columnas_y_orden_por_ultima_modificacion(self):
        viejo = self.sobre('10000', codigo='111')
        nuevo = self.sobre('20000', codigo='222', ubicacion='GBB-1-1')
        Sobre.objects.filter(pk=viejo.pk).update(actualizado_el=datetime(2026, 1, 1, tzinfo=dt_timezone.utc))
        res = self.listar()
        self.assertEqual((res['total'], res['limite']), (2, 500))
        self.assertEqual([s['id'] for s in res['data']], [nuevo.id, viejo.id])
        fila = res['data'][0]
        for campo in ('of_origen', 'proceso', 'codigo', 'ubicacion', 'estado', 'estado_display', 'actualizado_el'):
            self.assertIn(campo, fila)
        self.assertEqual((fila['codigo'], fila['ubicacion'], fila['estado_display']), ('222', 'GBB-1-1', 'Activo'))

    def test_busqueda_por_of_codigo_y_ubicacion(self):
        a = self.sobre('10567', codigo='S-1')
        b = self.sobre('20000', codigo='10999')
        c = self.sobre('30000', codigo='X', ubicacion='GAB-7-2')
        self.assertEqual({s['id'] for s in self.listar('10567')['data']}, {a.id})
        self.assertEqual({s['id'] for s in self.listar('109')['data']}, {b.id})
        self.assertEqual({s['id'] for s in self.listar('gab-7')['data']}, {c.id})
        self.assertEqual(self.listar('nada')['total'], 0)

    def test_tope_de_500_con_total(self):
        for i in range(3):
            self.sobre(f'4000{i}')
        with mock.patch.object(selectors, 'LIMITE_SOBRES', 2):
            res = self.listar()
        self.assertEqual((len(res['data']), res['total']), (2, 3))

    def test_editar_ubicacion(self):
        sobre = self.sobre('10000')
        respuesta = self.post('api_sobre_ubicacion', sobre.id, {'ubicacion': 'gbb-2-1'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual((respuesta.json()['ubicacion'], respuesta.json()['cambio']), ('GBB-2-1', True))
        self.assertEqual(HistorialUbicacionSobre.objects.get().usuario, self.operario)

        respuesta = self.post('api_sobre_ubicacion', sobre.id, {'ubicacion': 'Cajón'})
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('no es válida', respuesta.json()['message'])
        self.assertEqual(self.post('api_sobre_ubicacion', 999999, {'ubicacion': 'GBB-1-1'}).status_code, 404)
        self.assertEqual(self.client.get(reverse('grabados:api_sobre_ubicacion', args=[sobre.id])).status_code, 405)

    def test_desactivar_y_reactivar_por_api(self):
        sobre = self.sobre('10000')
        self.assertEqual(self.post('api_sobre_desactivar', sobre.id, {'motivo': 'no'}).status_code, 400)
        respuesta = self.post('api_sobre_desactivar', sobre.id, {'motivo': 'Sobre dañado'})
        self.assertEqual((respuesta.status_code, respuesta.json()['estado']), (200, 'INACTIVO'))
        fila = self.listar()['data'][0]
        self.assertEqual((fila['estado'], fila['motivo_desactivacion'], fila['desactivado_por']),
                         ('INACTIVO', 'Sobre dañado', 'operario'))
        self.assertEqual(self.post('api_sobre_ubicacion', sobre.id, {'ubicacion': 'GBB-1-1'}).status_code, 409)

        respuesta = self.post('api_sobre_reactivar', sobre.id)
        self.assertEqual((respuesta.status_code, respuesta.json()['estado']), (200, 'ACTIVO'))
        self.assertEqual(self.post('api_sobre_reactivar', sobre.id).status_code, 409)
        self.assertEqual(self.client.get(reverse('grabados:api_sobre_desactivar', args=[sobre.id])).status_code, 405)


class SobresFiltrosColumnaTests(BaseGrabados):
    """Filtros por columna de la pantalla Sobres (embudo), en el servidor."""

    def setUp(self):
        self.client.force_login(self.operario)
        self.a = Sobre.objects.create(of_origen='10000', proceso='STAMPING', codigo='111', ubicacion='GBB-1-1')
        self.b = Sobre.objects.create(of_origen='20000', proceso='EMBOSSING', codigo='', ubicacion=None)
        self.c = Sobre.objects.create(of_origen='30000', proceso='STAMPING', codigo='333', ubicacion='GBB-2-2',
                                      estado='INACTIVO')
        Sobre.objects.filter(pk=self.a.pk).update(actualizado_el=datetime(2026, 1, 10, 12, tzinfo=dt_timezone.utc))

    def filtrar(self, *filtros, q=''):
        respuesta = self.client.get(reverse('grabados:api_sobres'), {'q': q, 'filtros': json.dumps(list(filtros))})
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return {s['id'] for s in respuesta.json()['data']}

    def test_incluir_y_excluir_valores(self):
        self.assertEqual(self.filtrar({'col': 'proceso', 'in': ['STAMPING']}), {self.a.id, self.c.id})
        self.assertEqual(self.filtrar({'col': 'estado', 'fuera': ['INACTIVO']}), {self.a.id, self.b.id})
        self.assertEqual(self.filtrar({'col': 'codigo', 'in': ['—']}), {self.b.id})        # vacío
        self.assertEqual(self.filtrar({'col': 'ubicacion', 'fuera': ['—']}), {self.a.id, self.c.id})

    def test_rango_de_ultima_modificacion(self):
        self.assertEqual(self.filtrar({'col': 'modificado', 'desde': '2026-01-01', 'hasta': '2026-01-31'}),
                         {self.a.id})
        self.assertNotIn(self.a.id, self.filtrar({'col': 'modificado', 'desde': '2026-02-01'}))

    def test_se_combina_con_la_busqueda_y_el_total(self):
        self.assertEqual(self.filtrar({'col': 'proceso', 'in': ['STAMPING']}, q='333'), {self.c.id})
        respuesta = self.client.get(reverse('grabados:api_sobres'),
                                    {'filtros': json.dumps([{'col': 'proceso', 'in': ['STAMPING']}])})
        self.assertEqual(respuesta.json()['total'], 2)

    def test_filtros_invalidos_dan_400(self):
        for filtros in ('no-es-json', json.dumps([{'col': 'cliente', 'in': ['X']}]),
                        json.dumps([{'col': 'modificado', 'desde': 'ayer'}]), json.dumps({'col': 'of'})):
            with self.subTest(filtros=filtros):
                respuesta = self.client.get(reverse('grabados:api_sobres'), {'filtros': filtros})
                self.assertEqual(respuesta.status_code, 400)

    def test_valores_de_una_columna_con_etiquetas(self):
        respuesta = self.client.get(reverse('grabados:api_sobres_valores'), {'col': 'estado'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(set(respuesta.json()['valores']), {'ACTIVO', 'INACTIVO'})
        self.assertEqual(respuesta.json()['etiquetas'], {'ACTIVO': 'Activo', 'INACTIVO': 'Inactivo'})
        respuesta = self.client.get(reverse('grabados:api_sobres_valores'), {'col': 'codigo', 'q': '10000'})
        self.assertEqual(respuesta.json()['valores'], ['111'])

    def test_valores_columna_desconocida_y_login(self):
        self.assertEqual(self.client.get(reverse('grabados:api_sobres_valores'), {'col': 'fecha'}).status_code, 400)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('grabados:api_sobres_valores'), {'col': 'of'}).status_code, 302)

    def test_pagina_con_embudo_en_cada_columna(self):
        respuesta = self.client.get(reverse('grabados:sobres'))
        columnas = {'of', 'proceso', 'codigo', 'ubicacion', 'estado', 'modificado'}
        for col in columnas:
            with self.subTest(col=col):
                self.assertContains(respuesta, f'data-columna="{col}"')
                self.assertContains(respuesta, f'id="sob-punto-{col}"')
        self.assertEqual(set(selectors.CAMPOS_SOBRES) | set(selectors.CAMPOS_FECHA_SOBRES), columnas)
        for id_html in ('filtro-columna-panel', 'sob-quitar-columnas', 'sob-limpiar-columna'):
            self.assertContains(respuesta, f'id="{id_html}"')
        self.assertContains(respuesta, 'js/componentes/filtro_columna.js')


class SobreAdminTests(BaseGrabados):

    def test_admin_no_elimina_sobres_y_la_ubicacion_es_de_solo_lectura(self):
        sobre = Sobre.objects.create(of_origen='10000', proceso='STAMPING')
        modelo_admin = admin.site._registry[Sobre]
        peticion = RequestFactory().get('/')
        peticion.user = self.admin
        self.assertFalse(modelo_admin.has_delete_permission(peticion, sobre))
        self.assertNotIn('delete_selected', modelo_admin.get_actions(peticion))
        for campo in ('ubicacion', 'estado', 'desactivado_por', 'desactivado_el', 'motivo_desactivacion'):
            self.assertIn(campo, modelo_admin.get_readonly_fields(peticion, sobre))
        self.client.force_login(self.admin)
        url = reverse('admin:gestion_grabados_sobre_delete', args=[sobre.id])
        self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 403)
        self.assertTrue(Sobre.objects.filter(pk=sobre.pk).exists())

    def test_historiales_de_sobre_son_de_solo_lectura_y_protegen(self):
        peticion = mock.Mock(user=self.admin)
        for modelo in (HistorialUbicacionSobre, HistorialActivacionSobre):
            modelo_admin = admin.site._registry[modelo]
            with self.subTest(modelo=modelo.__name__):
                self.assertFalse(modelo_admin.has_add_permission(peticion))
                self.assertFalse(modelo_admin.has_change_permission(peticion))
                self.assertFalse(modelo_admin.has_delete_permission(peticion))
                self.assertEqual(modelo._meta.get_field('sobre').remote_field.on_delete.__name__, 'PROTECT')
        self.assertEqual(Grabado._meta.get_field('sobre_asociado').remote_field.on_delete.__name__, 'PROTECT')


# ============================================================
# ETIQUETA 6x4 CON CÓDIGO DE BARRAS (paso 3)
# ============================================================

def png_de_prueba(ancho=20, alto=10):
    from PIL import Image
    salida = BytesIO()
    Image.new('RGB', (ancho, alto), (200, 30, 30)).save(salida, format='PNG')
    return salida.getvalue()


class Codigo128Tests(SimpleTestCase):

    def test_tabla_de_patrones_completa_y_consistente(self):
        self.assertEqual(len(codigo128.PATRONES), 107)
        self.assertEqual(len(set(codigo128.PATRONES)), 107)
        for valor, patron in enumerate(codigo128.PATRONES):
            with self.subTest(valor=valor):
                self.assertEqual(sum(map(int, patron)), 13 if valor == codigo128.STOP else 11)

    def test_digito_de_control_calculado_a_mano(self):
        # Start B 104 + P(48)*1 + J(42)*2 + J(42)*3 + 1(17)*4 + 2(18)*5 + 3(19)*6 + C(35)*7 = 879; 879 % 103 = 55
        self.assertEqual(codigo128.valores('PJJ123C'), [104, 48, 42, 42, 17, 18, 19, 35, 55, 106])

    def test_las_barras_se_leen_de_vuelta(self):
        anchos = codigo128.modulos('22741-S')
        patron_a_valor = {p: v for v, p in enumerate(codigo128.PATRONES)}
        simbolos = [''.join(map(str, anchos[i:i + 6])) for i in range(0, len(anchos) - 7, 6)]
        valores = [patron_a_valor[s] for s in simbolos]
        self.assertEqual(valores[0], codigo128.INICIO_B)
        self.assertEqual(''.join(chr(v + 32) for v in valores[1:-1]), '22741-S')
        self.assertEqual(valores[-1], codigo128.valores('22741-S')[-2])   # dígito de control
        self.assertEqual(''.join(map(str, anchos[-7:])), codigo128.PATRONES[codigo128.STOP])

    def test_caracteres_invalidos_o_vacio(self):
        with self.assertRaises(codigo128.CodigoInvalido):
            codigo128.valores('22741-Ñ')
        with self.assertRaises(codigo128.CodigoInvalido):
            codigo128.valores('')

    def test_svg_dibuja_una_barra_por_cada_negro(self):
        svg = codigo128.svg('22741-S')
        # Start + 7 caracteres + control = 9 símbolos de 3 barras, más 4 barras del Stop.
        self.assertEqual(svg.count('<rect'), 9 * 3 + 4)
        self.assertIn('aria-label="22741-S"', svg)
        self.assertIn('preserveAspectRatio="none"', svg)


class ValorCodigoBarrasTests(SimpleTestCase):

    def test_of_mas_letra_del_proceso(self):
        self.assertEqual(selectors.valor_codigo_barras('22741', 'STAMPING'), '22741-S')
        self.assertEqual(selectors.valor_codigo_barras('22741', 'EMBOSSING'), '22741-E')


class ConfiguracionEtiquetaTests(BaseGrabados):

    def test_sin_configuracion_usa_los_valores_por_defecto(self):
        self.assertEqual(servicio_etiquetas.normalizar_campos(None), servicio_etiquetas.CAMPOS_POR_DEFECTO)
        config = selectors.configuracion_etiqueta()
        self.assertFalse(ConfiguracionEtiqueta.objects.exists())   # leer no crea la fila
        self.assertEqual([c['clave'] for c in config['campos']],
                         [clave for clave, _ in servicio_etiquetas.CAMPOS_ETIQUETA])
        self.assertTrue(config['logo_url'].endswith('img/logo/Imagen1.png'))
        self.assertFalse(config['logo_propio'])

    def test_completa_lo_que_falta_redondea_y_mete_el_campo_en_la_etiqueta(self):
        campos = servicio_etiquetas.normalizar_campos(
            {'ubicacion': {'x': 5.5, 'y': -1, 'tamano': 50.04, 'visible': False}})
        self.assertEqual(campos['ubicacion'], {'visible': False, 'x': 2.4, 'y': 0.0, 'ancho': 3.6,
                                               'alto': 0.8, 'tamano': 50.0})
        self.assertEqual(campos['of'], servicio_etiquetas.CAMPOS_POR_DEFECTO['of'])

    def test_valores_invalidos(self):
        casos = [
            ({'otro': {}}, 'Campo desconocido'),
            ({'of': {'ancho': 6.5}}, 'el ancho'),
            ({'of': {'alto': 0}}, 'el alto'),
            ({'of': {'tamano': 200}}, 'tamaño de letra'),
            ({'of': {'x': '1'}}, 'tiene que ser un número'),
            ({'of': {'visible': 'si'}}, 'verdadero o falso'),
            ([], 'formato válido'),
        ]
        for campos, mensaje in casos:
            with self.subTest(campos=campos):
                with self.assertRaisesMessage(servicio_etiquetas.ConfiguracionInvalida, mensaje):
                    servicio_etiquetas.normalizar_campos(campos)

    def test_guardar_usa_siempre_la_misma_fila(self):
        servicio_etiquetas.guardar_configuracion(campos={'of': {'tamano': 40}}, usuario=self.admin)
        servicio_etiquetas.guardar_configuracion(campos={'of': {'tamano': 42}}, usuario=self.admin)
        config = ConfiguracionEtiqueta.objects.get()
        self.assertEqual(config.pk, 1)
        self.assertEqual(config.campos['of']['tamano'], 42)
        self.assertEqual(config.actualizado_por, self.admin)

    def test_logo_en_la_base_y_volver_al_por_defecto(self):
        png = png_de_prueba()
        servicio_etiquetas.guardar_configuracion(campos={}, usuario=self.admin, logo=png)
        config = ConfiguracionEtiqueta.objects.get()
        self.assertEqual((bytes(config.logo), config.logo_tipo), (png, 'image/png'))
        self.assertTrue(selectors.configuracion_etiqueta()['logo_url'].startswith('data:image/png;base64,'))

        # Guardar sin logo nuevo conserva el que había.
        servicio_etiquetas.guardar_configuracion(campos={}, usuario=self.admin)
        self.assertTrue(selectors.configuracion_etiqueta()['logo_propio'])

        servicio_etiquetas.guardar_configuracion(campos={}, usuario=self.admin, quitar_logo=True)
        self.assertFalse(selectors.configuracion_etiqueta()['logo_propio'])

    def test_logo_invalido(self):
        with self.assertRaisesMessage(servicio_etiquetas.ConfiguracionInvalida, 'PNG o JPG'):
            servicio_etiquetas.validar_logo(b'no es una imagen')
        with self.assertRaisesMessage(servicio_etiquetas.ConfiguracionInvalida, '1 MB'):
            servicio_etiquetas.validar_logo(b'x' * (servicio_etiquetas.LOGO_MAXIMO_BYTES + 1))
        with self.assertRaisesMessage(servicio_etiquetas.ConfiguracionInvalida, 'vacío'):
            servicio_etiquetas.validar_logo(b'')

    def test_una_fila_danada_no_deja_sin_etiquetas(self):
        ConfiguracionEtiqueta.objects.create(pk=1, campos={'of': {'ancho': 'mal'}})
        config = selectors.configuracion_etiqueta()
        self.assertEqual(config['campos_dict'], servicio_etiquetas.CAMPOS_POR_DEFECTO)


class EtiquetaVistasTests(BasePlani):

    def setUp(self):
        self.client.force_login(self.operario)

    def url_grabado(self, grabado, imprimir=False):
        url = reverse('grabados:etiqueta_grabado', args=[grabado.id])
        return f'{url}?imprimir=1' if imprimir else url

    def test_etiqueta_del_grabado(self):
        grabado = self.aprobado()
        servicio.cambiar_ubicacion(grabado_id=grabado.id, ubicacion='gbb-2-1', usuario=self.operario)
        grabado.refresh_from_db()
        respuesta = self.client.get(self.url_grabado(grabado))
        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.content.decode()
        for texto in ('OF 22741', 'STAMPING', 'CLIENTE SA', 'ANILLA DORADA', 'GBB-2-1',
                      selectors.formatear_fecha(grabado.actualizado_el), '22741-S', '<svg',
                      'size: 6in 4in', 'Imagen1.png'):
            with self.subTest(texto=texto):
                self.assertIn(texto, html)
        self.assertEqual(respuesta['X-Frame-Options'], 'SAMEORIGIN')   # se imprime desde un iframe
        self.assertNotIn("window.addEventListener('load'", html)       # sin ?imprimir=1 no abre el diálogo

    def test_con_imprimir_abre_el_dialogo_al_cargar(self):
        grabado = self.aprobado()
        html = self.client.get(self.url_grabado(grabado, imprimir=True)).content.decode()
        self.assertIn("window.addEventListener('load', function () { window.print(); });", html)

    def test_embossing_y_grabado_inactivo(self):
        grabado = self.aprobado(of='30000', proceso='EMBOSSING')
        servicio.desactivar_grabado(grabado_id=grabado.id, motivo='Ya no se usa', usuario=self.operario)
        respuesta = self.client.get(self.url_grabado(grabado))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, '30000-E')

    def test_etiqueta_del_sobre_usa_ubicacion_y_fecha_del_sobre(self):
        grabado = self.registrar(ubicacion_sobre='SOB-9-9')['grabado']
        servicio.cambiar_ubicacion(grabado_id=grabado.id, ubicacion='GBB-1-1', usuario=self.operario)
        sobre = Sobre.objects.get(of_origen='22741', proceso='STAMPING')
        respuesta = self.client.get(reverse('grabados:etiqueta_sobre', args=[sobre.id]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'SOB-9-9')
        self.assertNotContains(respuesta, 'GBB-1-1')
        self.assertContains(respuesta, 'ANILLA DORADA')   # descripción del grabado enlazado
        self.assertContains(respuesta, selectors.formatear_fecha(sobre.actualizado_el))
        self.assertContains(respuesta, '22741-S')

    def test_sobre_sin_grabado_enlazado(self):
        sobre = Sobre.objects.create(of_origen='10000', proceso='EMBOSSING')
        datos = selectors.datos_etiqueta_sobre(sobre.id)
        self.assertEqual((datos['cliente'], datos['descripcion'], datos['ubicacion'], datos['codigo']),
                         ('—', '—', '—', '10000-E'))

    def test_no_existe_da_404(self):
        self.assertEqual(self.client.get(reverse('grabados:etiqueta_grabado', args=[999999])).status_code, 404)
        self.assertEqual(self.client.get(reverse('grabados:etiqueta_sobre', args=[999999])).status_code, 404)

    def test_pagina_de_prueba(self):
        respuesta = self.client.get(reverse('grabados:etiqueta_prueba'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Página de prueba')
        self.assertContains(respuesta, '22741-S')
        self.assertContains(respuesta, 'GBB-2-1')

    def test_la_etiqueta_usa_el_diseno_guardado(self):
        servicio_etiquetas.guardar_configuracion(
            campos={'ubicacion': {'visible': False}, 'of': {'tamano': 50, 'x': 1.25}},
            usuario=self.admin, logo=png_de_prueba())
        html = self.client.get(reverse('grabados:etiqueta_prueba')).content.decode()
        self.assertNotIn('data-campo="ubicacion"', html)
        self.assertIn('left:1.25in;', html)
        self.assertIn('font-size:50.0pt;', html)
        self.assertIn('data:image/png;base64,', html)
        self.assertNotIn('Imagen1.png', html)

    def test_no_se_guarda_control_de_impresiones(self):
        grabado = self.aprobado()
        antes = grabado.actualizado_el
        self.client.get(self.url_grabado(grabado, imprimir=True))
        grabado.refresh_from_db()
        self.assertEqual(grabado.actualizado_el, antes)


class EtiquetaBotonesTests(BaseGrabados):

    def test_las_pantallas_cargan_el_componente(self):
        self.client.force_login(self.operario)
        for nombre in ('inventario_grabados', 'grabado_consulta', 'sobres', 'plani_consulta'):
            with self.subTest(pantalla=nombre):
                self.assertContains(self.client.get(reverse(f'grabados:{nombre}')), 'js/componentes/etiqueta.js')

    def test_botones_de_etiqueta_habilitados(self):
        inventario = (RUTA_JS / 'inventario_grabados.js').read_text(encoding='utf-8')
        self.assertNotIn('Disponible próximamente', inventario)
        self.assertIn('Etiqueta.imprimirGrabado(imprimir.dataset.id)', inventario)
        self.assertIn('Etiqueta.imprimirSobre(sobre.id)', (RUTA_JS / 'sobres.js').read_text(encoding='utf-8'))
        tabla = (RUTA_JS / 'grabados_tabla.js').read_text(encoding='utf-8')
        self.assertIn('Etiqueta.imprimirGrabado(etiqueta.dataset.etiqueta)', tabla)
        self.assertIn('data-etiqueta="${reg.grabado.id}"', tabla)
        self.assertIn('Etiqueta.imprimirGrabado(res.grabado_id)',
                      (RUTA_JS / 'plani_tabla.js').read_text(encoding='utf-8'))


class DisenadorEtiquetaAdminTests(BaseGrabados):

    URL = '/crdadmin/gestion_grabados/configuracionetiqueta/1/change/'

    def datos(self, **cambios):
        datos = {'campos_json': json.dumps(servicio_etiquetas.CAMPOS_POR_DEFECTO), '_save': 'Guardar'}
        datos.update(cambios)
        return datos

    def test_la_lista_lleva_al_disenador_sin_crear_la_fila(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('admin:gestion_grabados_configuracionetiqueta_changelist'))
        self.assertRedirects(respuesta, self.URL)
        respuesta = self.client.get(self.URL)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'id="de-campos"')
        self.assertContains(respuesta, 'id="id_campos_json"')
        self.assertContains(respuesta, reverse('grabados:etiqueta_prueba'))
        self.assertContains(respuesta, 'enctype="multipart/form-data"')
        self.assertFalse(ConfiguracionEtiqueta.objects.exists())

    def test_guardar_diseno_y_logo(self):
        self.client.force_login(self.admin)
        campos = dict(servicio_etiquetas.CAMPOS_POR_DEFECTO)
        campos['fecha'] = {**campos['fecha'], 'visible': False}
        logo = SimpleUploadedFile('logo.png', png_de_prueba(), content_type='image/png')
        respuesta = self.client.post(self.URL, self.datos(campos_json=json.dumps(campos), logo_archivo=logo))
        self.assertEqual(respuesta.status_code, 302, respuesta.content[:2000])
        config = ConfiguracionEtiqueta.objects.get()
        self.assertFalse(config.campos['fecha']['visible'])
        self.assertEqual((config.logo_tipo, config.actualizado_por), ('image/png', self.admin))

    def test_diseno_o_logo_invalidos_no_guardan(self):
        self.client.force_login(self.admin)
        respuesta = self.client.post(self.URL, self.datos(campos_json=json.dumps({'of': {'tamano': 500}})))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'tamaño de letra')
        logo = SimpleUploadedFile('logo.png', b'no es imagen', content_type='image/png')
        respuesta = self.client.post(self.URL, self.datos(logo_archivo=logo))
        self.assertContains(respuesta, 'PNG o JPG')
        self.assertFalse(ConfiguracionEtiqueta.objects.exists())

    def test_solo_superusuario(self):
        staff = User.objects.create_user('staff', password='x', is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='gestion_grabados', codename__endswith='configuracionetiqueta'))
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.URL).status_code, 403)
        self.assertEqual(self.client.post(self.URL, self.datos()).status_code, 403)
        self.assertFalse(ConfiguracionEtiqueta.objects.exists())

        modelo_admin = admin.site._registry[ConfiguracionEtiqueta]
        peticion = mock.Mock(user=self.admin)
        self.assertFalse(modelo_admin.has_add_permission(peticion))
        self.assertFalse(modelo_admin.has_delete_permission(peticion))
        peticion = mock.Mock(user=staff)
        self.assertFalse(modelo_admin.has_module_permission(peticion))


# ============================================================
# CONSULTOR DE OF (solo lectura)
# ============================================================

RUTA_CONSULTAR_OF = 'apps.gestion_grabados.views_grabados.consultar_of_externa'


def fila_api(of_stamping=None, of_embossing=None, acabat_stamping=None, acabat_embossing=None,
             cliente='CLIENTE SA', referencia='ANILLA DORADA', sobre='S-10'):
    """Misma forma que devuelve views.consultar_of_externa()."""
    return {'Sobre_pelicula': sobre, 'OF_Stamping': of_stamping, 'OF_Embossing': of_embossing,
            'Acabat_Stamping': acabat_stamping, 'Acabat_Embossing': acabat_embossing,
            'G_Cliente': cliente, 'G_Referencia': referencia}


class ConsultarOfExternaTests(SimpleTestCase):
    """views.consultar_of_externa: distingue "no existe" (None) de "no responde" (excepción)."""

    def test_devuelve_la_fila_por_columna(self):
        from apps.gestion_grabados import views
        fila = ('S-10', 22741.0, None, '1', None, 'CLIENTE SA', 'ANILLA')
        with mock.patch.object(views, '_buscar_fila_db', return_value=fila) as buscar:
            resultado = views.consultar_of_externa(22741)
        buscar.assert_called_once_with(22741, views.COLUMNAS_INFO_EXTERNA)
        self.assertEqual(resultado['OF_Stamping'], 22741.0)
        self.assertEqual(resultado['G_Referencia'], 'ANILLA')

    @override_settings(EXTERNA_2012_SOURCE='api')
    def test_usa_la_api_segun_la_configuracion_y_none_si_no_existe(self):
        from apps.gestion_grabados import views
        with mock.patch.object(views, '_buscar_fila_api', return_value=None) as buscar:
            self.assertIsNone(views.consultar_of_externa(1))
        buscar.assert_called_once()

    def test_si_el_origen_falla_lanza_error(self):
        from apps.gestion_grabados import views
        with mock.patch.object(views, '_buscar_fila_db', side_effect=OSError('timeout')):
            with self.assertRaises(views.ErrorDatosExternos):
                views.consultar_of_externa(22741)


class ConsultorOfTests(BaseGrabados):

    def consultar(self, of, fila=None, error=None):
        self.client.force_login(self.operario)
        kwargs = {'side_effect': error} if error else {'return_value': fila}
        with mock.patch(RUTA_CONSULTAR_OF, **kwargs) as consulta:
            respuesta = self.client.get(reverse('grabados:api_consultor_of'), {'of': of})
        return respuesta, consulta

    def proceso(self, datos, proceso):
        return next(p for p in datos['procesos'] if p['proceso'] == proceso)

    def test_pagina_y_enlace_en_el_menu(self):
        self.client.force_login(self.operario)
        respuesta = self.client.get(reverse('grabados:consultor_of'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, 'consultor_of.html')
        self.assertContains(respuesta, f'href="{reverse("grabados:consultor_of")}"')
        self.assertContains(respuesta, 'Consultor de OF')

    def test_of_vacia_o_invalida_da_400_sin_consultar_la_api(self):
        for valor in ('', '   ', 'abc'):
            with self.subTest(of=valor):
                respuesta, consulta = self.consultar(valor)
                self.assertEqual(respuesta.status_code, 400)
                self.assertEqual(respuesta.json()['message'], 'Ingresa un número de OF válido.')
                consulta.assert_not_called()

    def test_normaliza_la_of(self):
        respuesta, consulta = self.consultar(' 22741.0 ', fila_api(of_stamping='22741'))
        self.assertEqual(respuesta.status_code, 200)
        consulta.assert_called_once_with(22741)
        self.assertEqual(respuesta.json()['of'], '22741')

    def test_api_caida_da_503_con_mensaje(self):
        from apps.gestion_grabados.views import ErrorDatosExternos
        respuesta, _ = self.consultar('22741', error=ErrorDatosExternos('timeout'))
        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(respuesta.json()['status'], 'error')
        self.assertIn('no responde', respuesta.json()['message'])

    def test_of_que_la_api_no_encuentra(self):
        respuesta, _ = self.consultar('99999', None)
        datos = respuesta.json()
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(datos['encontrado'])
        self.assertEqual(datos['datos'], {'cliente': None, 'descripcion': None, 'sobre': None,
                                          'of_referencia': None})
        for p in datos['procesos']:
            self.assertIsNone(p['grabado'])
            self.assertEqual(p['of_origen'], '99999')
            self.assertFalse(p['sin_proceso_en_api'])

    def test_of_que_la_api_no_encuentra_igual_muestra_su_grabado(self):
        grabado = self.registrar(of='22741')['grabado']
        datos = self.consultar('22741', None)[0].json()
        self.assertEqual(self.proceso(datos, 'STAMPING')['grabado']['id'], grabado.id)

    def test_datos_tal_como_vienen_y_vacios_en_none(self):
        fila = fila_api(of_stamping=22741.0, acabat_stamping='1', cliente='  CLIENTE   SA ', referencia='',
                        sobre='—')
        datos = self.consultar('22741', fila)[0].json()
        self.assertTrue(datos['encontrado'])
        self.assertEqual(datos['datos'], {'cliente': 'CLIENTE SA', 'descripcion': None, 'sobre': None,
                                          'of_referencia': '22741'})
        stamping, embossing = self.proceso(datos, 'STAMPING'), self.proceso(datos, 'EMBOSSING')
        self.assertEqual((stamping['of_referencia'], stamping['acabado'], stamping['terminada']), ('22741', '1', True))
        self.assertEqual((embossing['of_referencia'], embossing['acabado'], embossing['terminada']), (None, None, False))
        self.assertTrue(embossing['sin_proceso_en_api'])

    def test_grabado_existente_con_estado_y_ubicacion(self):
        grabado = self.registrar(of='22741')['grabado']
        Grabado.objects.filter(pk=grabado.pk).update(ubicacion='GBB-2-1')
        grabado.refresh_from_db()
        datos = self.consultar('22741', fila_api(of_stamping='22741'))[0].json()
        stamping = self.proceso(datos, 'STAMPING')
        self.assertEqual(stamping['grabado'], {
            'id': grabado.id, 'of_origen': '22741', 'estado': 'PENDIENTE_K1',
            'estado_display': grabado.get_estado_display(), 'tipo': 'K1',
            'tipo_display': grabado.get_tipo_display(), 'ubicacion': 'GBB-2-1',
        })
        self.assertFalse(stamping['usa_grabado_de_otra'])
        self.assertIsNone(self.proceso(datos, 'EMBOSSING')['grabado'])

    def test_of_que_usa_el_grabado_de_su_referencia(self):
        grabado = self.registrar(of='22741')['grabado']
        datos = self.consultar('25000', fila_api(of_stamping='22741'))[0].json()
        stamping = self.proceso(datos, 'STAMPING')
        self.assertEqual(stamping['of_origen'], '22741')
        self.assertTrue(stamping['usa_grabado_de_otra'])
        self.assertEqual(stamping['grabado']['id'], grabado.id)

    def test_dos_procesos_con_referencias_distintas_sin_referencia_general(self):
        datos = self.consultar('25000', fila_api(of_stamping='22741', of_embossing='22800'))[0].json()
        self.assertIsNone(datos['datos']['of_referencia'])
        self.assertEqual(self.proceso(datos, 'EMBOSSING')['of_origen'], '22800')

    def test_no_escribe_nada(self):
        self.registrar(of='22741')
        modelos = (Grabado, FabricacionGrabado, PruebaK1, Sobre, HistorialUbicacion, EnvioMaquina, EstadoBano)
        antes = [m.objects.count() for m in modelos]
        self.consultar('22741', fila_api(of_stamping='22741'))
        self.consultar('30000', None)
        self.assertEqual([m.objects.count() for m in modelos], antes)

    def test_solo_get(self):
        self.client.force_login(self.operario)
        with mock.patch(RUTA_CONSULTAR_OF) as consulta:
            respuesta = self.client.post(reverse('grabados:api_consultor_of'), {'of': '22741'})
        self.assertEqual(respuesta.status_code, 405)
        consulta.assert_not_called()

    def test_js_enlaza_crear_grabado_y_el_panel(self):
        js = (RUTA_JS / 'consultor_of.js').read_text(encoding='utf-8')
        self.assertIn('/grabados/alta/?', js)
        self.assertIn('Sin grabado en EIS', js)
        self.assertIn('PanelGrabado.abrir', js)
