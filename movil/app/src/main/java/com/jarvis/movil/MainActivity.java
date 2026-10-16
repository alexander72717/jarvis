package com.jarvis.movil;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.hardware.camera2.CameraManager;
import android.net.Uri;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Bundle;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.Locale;

/**
 * Contenedor nativo de JARVIS Móvil: una WebView con la interfaz y un puente
 * "Android" que le da a JavaScript red sin CORS, voz (reconocimiento + TTS),
 * linterna, vibración y batería.
 */
public class MainActivity extends Activity {
    private WebView web;
    private TextToSpeech tts;
    private boolean ttsListo = false;
    private SpeechRecognizer reconocedor;
    private long ultimoNivel = 0;
    private static final int PEDIR_MIC = 7;

    @Override
    protected void onCreate(Bundle estado) {
        super.onCreate(estado);
        getWindow().setStatusBarColor(Color.rgb(2, 6, 13));
        getWindow().setNavigationBarColor(Color.rgb(2, 6, 13));

        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(2, 6, 13));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(true);
        web.setWebChromeClient(new WebChromeClient());
        web.addJavascriptInterface(new Puente(), "Android");
        setContentView(web);
        web.loadUrl("file:///android_asset/www/index.html");

        tts = new TextToSpeech(this, codigo -> {
            if (codigo != TextToSpeech.SUCCESS) return;
            int r = tts.setLanguage(new Locale("es", "CO"));
            if (r < 0) r = tts.setLanguage(new Locale("es", "MX"));
            if (r < 0) tts.setLanguage(new Locale("es"));
            tts.setPitch(0.92f);
            tts.setSpeechRate(1.05f);
            tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                @Override public void onStart(String id) { evento("voz_inicio", ""); }
                @Override public void onDone(String id) { evento("voz_fin", id); }
                @Override public void onError(String id) { evento("voz_fin", id); }
            });
            ttsListo = true;
        });
    }

    /** Llama a window.__nativo.evento(tipo, dato) en la página. */
    private void evento(String tipo, String dato) {
        final String js = "window.__nativo&&window.__nativo.evento(" + JSONObject.quote(tipo) + "," + JSONObject.quote(dato) + ")";
        web.post(() -> web.evaluateJavascript(js, null));
    }

    private void iniciarEscucha() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, PEDIR_MIC);
            return;
        }
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            evento("error_voz", "Este teléfono no tiene reconocimiento de voz (instala la app de Google).");
            return;
        }
        if (reconocedor == null) {
            reconocedor = SpeechRecognizer.createSpeechRecognizer(this);
            reconocedor.setRecognitionListener(new RecognitionListener() {
                @Override public void onReadyForSpeech(Bundle b) { evento("escuchando", ""); }
                @Override public void onBeginningOfSpeech() { }
                @Override public void onRmsChanged(float rms) {
                    long ahora = System.currentTimeMillis();
                    if (ahora - ultimoNivel > 60) { ultimoNivel = ahora; evento("nivel", String.valueOf(rms)); }
                }
                @Override public void onBufferReceived(byte[] b) { }
                @Override public void onEndOfSpeech() { evento("fin_habla", ""); }
                @Override public void onError(int codigo) { evento("error_voz", String.valueOf(codigo)); }
                @Override public void onResults(Bundle b) {
                    ArrayList<String> r = b.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                    evento("final", r != null && !r.isEmpty() ? r.get(0) : "");
                }
                @Override public void onPartialResults(Bundle b) {
                    ArrayList<String> r = b.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                    if (r != null && !r.isEmpty()) evento("parcial", r.get(0));
                }
                @Override public void onEvent(int t, Bundle b) { }
            });
        }
        Intent i = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "es-CO");
        i.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        reconocedor.startListening(i);
    }

    @Override
    public void onRequestPermissionsResult(int codigo, String[] permisos, int[] resultados) {
        if (codigo == PEDIR_MIC) {
            if (resultados.length > 0 && resultados[0] == PackageManager.PERMISSION_GRANTED) iniciarEscucha();
            else evento("error_voz", "Sin permiso de micrófono.");
        }
    }

    @Override
    public void onBackPressed() {
        web.evaluateJavascript("window.__atras&&window.__atras()", valor -> {
            if (!"true".equals(valor)) MainActivity.super.onBackPressed();
        });
    }

    @Override
    protected void onDestroy() {
        if (tts != null) tts.shutdown();
        if (reconocedor != null) reconocedor.destroy();
        super.onDestroy();
    }

    class Puente {
        /** Petición HTTP sin restricciones de CORS. Responde con __nativo.resp(id, estado, cuerpo, esperaSegundos). */
        @JavascriptInterface
        public void http(final String id, final String metodo, final String url, final String cabeceras, final String cuerpo) {
            new Thread(() -> {
                int estado = 0;
                String texto, espera = "";
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                    c.setRequestMethod(metodo);
                    c.setConnectTimeout(15000);
                    c.setReadTimeout(90000);
                    c.setRequestProperty("User-Agent", "Mozilla/5.0 (Linux; Android) JARVIS-Movil");
                    JSONObject h = new JSONObject(cabeceras == null || cabeceras.isEmpty() ? "{}" : cabeceras);
                    Iterator<String> k = h.keys();
                    while (k.hasNext()) { String n = k.next(); c.setRequestProperty(n, h.getString(n)); }
                    if (cuerpo != null && !cuerpo.isEmpty()) {
                        c.setDoOutput(true);
                        OutputStream os = c.getOutputStream();
                        os.write(cuerpo.getBytes("UTF-8"));
                        os.close();
                    }
                    estado = c.getResponseCode();
                    String ra = c.getHeaderField("retry-after");
                    if (ra != null) espera = ra;
                    InputStream in = estado >= 400 ? c.getErrorStream() : c.getInputStream();
                    texto = leer(in);
                } catch (Exception e) {
                    texto = "Error de red: " + e.getMessage();
                }
                final String js = "window.__nativo.resp(" + JSONObject.quote(id) + "," + estado + "," + JSONObject.quote(texto) + "," + JSONObject.quote(espera) + ")";
                web.post(() -> web.evaluateJavascript(js, null));
            }).start();
        }

        @JavascriptInterface
        public void hablar(String texto) {
            if (!ttsListo) { evento("voz_fin", ""); return; }
            Bundle b = new Bundle();
            tts.speak(texto, TextToSpeech.QUEUE_ADD, b, "u" + System.nanoTime());
        }

        @JavascriptInterface
        public void callar() { if (ttsListo) tts.stop(); evento("voz_fin", ""); }

        @JavascriptInterface
        public void escuchar() { runOnUiThread(MainActivity.this::iniciarEscucha); }

        @JavascriptInterface
        public void dejarDeEscuchar() { runOnUiThread(() -> { if (reconocedor != null) reconocedor.stopListening(); }); }

        @JavascriptInterface
        public void abrir(String url) {
            try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url))); } catch (Exception ignored) { }
        }

        @JavascriptInterface
        public String linterna(boolean encender) {
            try {
                CameraManager cm = (CameraManager) getSystemService(Context.CAMERA_SERVICE);
                cm.setTorchMode(cm.getCameraIdList()[0], encender);
                return "ok";
            } catch (Exception e) {
                return "error: " + e.getMessage();
            }
        }

        @JavascriptInterface
        public void vibrar(int ms) {
            Vibrator v = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
            if (v == null) return;
            if (Build.VERSION.SDK_INT >= 26) v.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE));
            else v.vibrate(ms);
        }

        @JavascriptInterface
        public String bateria() {
            Intent b = registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
            if (b == null) return "{}";
            int nivel = b.getIntExtra(BatteryManager.EXTRA_LEVEL, -1), escala = b.getIntExtra(BatteryManager.EXTRA_SCALE, 100);
            int st = b.getIntExtra(BatteryManager.EXTRA_STATUS, -1);
            boolean carga = st == BatteryManager.BATTERY_STATUS_CHARGING || st == BatteryManager.BATTERY_STATUS_FULL;
            return "{\"nivel\":" + (nivel * 100 / Math.max(escala, 1)) + ",\"cargando\":" + carga + "}";
        }

        @JavascriptInterface
        public String dispositivo() { return Build.MANUFACTURER + " " + Build.MODEL + " · Android " + Build.VERSION.RELEASE; }
    }

    private static String leer(InputStream in) throws Exception {
        if (in == null) return "";
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        in.close();
        return out.toString("UTF-8");
    }
}
