# Corrector de Postura

## Descripción del Proyecto
Un dispositivo diseñado para monitorear la postura de la espalda en tiempo real y alertar al usuario mediante vibraciones cuando detecta una mala inclinación.

### Integrantes
* **Isabel Sánchez Villalba** — Hardware / Diseño Físico
* **Juanita González Uyabán** — Hardware / PCB y Ensamble
* **Felipe Peña Velandia & Nicolás Aguilar** — Documentación, Gestión de GitHub y Código en MicroPython

##  Materiales, Componentes y Software
* **Microcontrolador:** ESP32-S3
* * **Sensor de movimiento:** MPU-6050 (Acelerómetro + Giroscopio)
* **Motores vibradores tipo moneda (x2)**
* **Transistor NPN 2N2222**
* **Diodos de protección (Flyback)**
* **Resistencia 1k ohms para la base del transistor**
* **Batería Li-Po 3.7 V**
* **Módulo cargador TP4056 con protección**
* **Convertidor elevador DC-DC (Step-Up) de 3.7 V a 5 V**
* **PCB personalizada**
* **Cables, conectores y pines macho/hembra**
* **Lenguaje:** MicroPython
* **Entorno de desarrollo (IDE):** Thonny IDE
* **Diseño 3D (Case):** Onshape
* **Laminador / Slicing:** Bambu Studio
* **Impresión 3D:** Impresora Bambu Lab
* **Diagramación:** Lucidchart
  
## ¿Cómo funciona?
1. El sensor mide el ángulo de inclinación de la espalda.
2. Si supera el límite de grados definido durante varios segundos, activa la alerta.
3. Al recuperar la postura correcta, la alerta se desactiva automáticamente.
