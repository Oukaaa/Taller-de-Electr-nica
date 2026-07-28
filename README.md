# Corrector de Postura

## Descripción del Proyecto
Un dispositivo diseñado para monitorear la postura de la espalda en tiempo real y alertar al usuario mediante vibraciones cuando detecta una mala inclinación.

## Equipo de Trabajo
* **Isabel** — Hardware / Diseño Físico
* **Juanita** — Hardware / PCB y Ensamble
* **Felipe & Nicolás** — Documentación, Gestión de GitHub y Código en MicroPython

##  Materiales, Componentes y Software
* **Microcontrolador:** ESP32-S3
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
