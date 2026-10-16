#include <gtk/gtk.h>
#include <stdio.h>

static FILE *events;
static int clicks;
static void changed(GtkEditable *entry, gpointer unused) {
    fprintf(events, "text %s\n", gtk_entry_get_text(GTK_ENTRY(entry)));
    fflush(events);
}
static void clicked(GtkButton *button, gpointer unused) {
    fprintf(events, "button pressed %d\n", ++clicks);
    fflush(events);
}
int main(int argc, char **argv) {
    events = fopen(argv[1], "w");
    if (!events) return 1;
    gtk_init(&argc, &argv);
    GtkWidget *window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_title(GTK_WINDOW(window), "Reasonix CUA Native Probe");
    gtk_window_set_default_size(GTK_WINDOW(window), 480, 280);
    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 20);
    gtk_container_add(GTK_CONTAINER(window), box);
    GtkWidget *entry = gtk_entry_new();
    atk_object_set_name(gtk_widget_get_accessible(entry), "Probe field");
    gtk_box_pack_start(GTK_BOX(box), entry, FALSE, FALSE, 0);
    GtkWidget *button = gtk_button_new_with_label("Probe button");
    atk_object_set_name(gtk_widget_get_accessible(button), "Probe button");
    gtk_box_pack_start(GTK_BOX(box), button, FALSE, FALSE, 0);
    g_signal_connect(entry, "changed", G_CALLBACK(changed), NULL);
    g_signal_connect(button, "clicked", G_CALLBACK(clicked), NULL);
    g_signal_connect(window, "destroy", G_CALLBACK(gtk_main_quit), NULL);
    gtk_widget_show_all(window);
    fprintf(events, "ready\n");
    fflush(events);
    gtk_main();
    fclose(events);
    return 0;
}
