import type { ProvisionTemplate } from '@/api'

/** Usuario y clave PPPoE y WiFi del cliente, como los escribe quien aprovisiona. */
export interface CustomerService {
  pppoeUser: string
  pppoePassword: string
  wifiName: string
  wifiKey: string
}

export function emptyCustomer(): CustomerService {
  return { pppoeUser: '', pppoePassword: '', wifiName: '', wifiKey: '' }
}

/** Lo que va a la API según el plan: solo lo que el plan usa. */
export function customerPayload(
  plan: ProvisionTemplate,
  customer: CustomerService,
): {
  template_id: string
  pppoe_user?: string
  pppoe_password?: string
  wifi_name?: string
  wifi_key?: string
} {
  return {
    template_id: plan.id,
    ...(plan.body.wan
      ? { pppoe_user: customer.pppoeUser.trim(), pppoe_password: customer.pppoePassword }
      : {}),
    ...(plan.body.wifi && customer.wifiName.trim()
      ? { wifi_name: customer.wifiName.trim(), wifi_key: customer.wifiKey }
      : {}),
  }
}

/** Las claves ya viajaron selladas: no se quedan en la pantalla. */
export function forgetSecrets(customer: CustomerService): void {
  customer.pppoePassword = ''
  customer.wifiKey = ''
}
